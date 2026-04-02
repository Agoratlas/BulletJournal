from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bulletjournal.config import DB_TIMEOUT_SECONDS
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, RunStatus, ValidationSeverity
from bulletjournal.domain.models import CheckpointRecord, ValidationIssue
from bulletjournal.storage.migrations import MIGRATIONS
from bulletjournal.utils import json_dumps, utc_now_iso


LOG_PREVIEW_MAX_LINES = 50
LOG_PREVIEW_MAX_CHARS = 10_000
SUPPORTED_DB_JOURNAL_MODES = frozenset({'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'WAL', 'OFF'})


def _database_journal_mode(path: Path, *, in_container: bool | None = None) -> str:
    configured = os.environ.get('BULLETJOURNAL_DB_JOURNAL_MODE')
    if configured is not None:
        candidate = configured.strip().upper()
        if candidate in SUPPORTED_DB_JOURNAL_MODES:
            return candidate
    if in_container is None:
        in_container = Path('/.dockerenv').exists()
    try:
        resolved_path = path.resolve()
    except OSError:
        resolved_path = path
    if in_container and resolved_path.is_relative_to(Path('/project')):
        return 'DELETE'
    return 'WAL'


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        self._journal_mode = _database_journal_mode(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=DB_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute(f'PRAGMA busy_timeout = {int(DB_TIMEOUT_SECONDS * 1000)}')
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute(f'PRAGMA journal_mode = {self._journal_mode}')
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            for name, sql in MIGRATIONS:
                exists = connection.execute(
                    'SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?', ('table', 'schema_migrations')
                ).fetchone()
                if exists:
                    applied = connection.execute('SELECT 1 FROM schema_migrations WHERE name = ?', (name,)).fetchone()
                    if applied:
                        continue
                connection.executescript(sql)
                connection.execute(
                    'INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)',
                    (name, utc_now_iso()),
                )
            self._ensure_orchestrator_execution_meta_columns(connection)
            connection.commit()

    def _ensure_orchestrator_execution_meta_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row['name']) for row in connection.execute('PRAGMA table_info(orchestrator_execution_meta)').fetchall()
        }
        if 'total_cells' not in columns:
            connection.execute('ALTER TABLE orchestrator_execution_meta ADD COLUMN total_cells INTEGER NULL')
        if 'last_completed_cell_number' not in columns:
            connection.execute(
                'ALTER TABLE orchestrator_execution_meta ADD COLUMN last_completed_cell_number INTEGER NULL'
            )
        if 'stdout_path' not in columns:
            connection.execute('ALTER TABLE orchestrator_execution_meta ADD COLUMN stdout_path TEXT NULL')
        if 'stderr_path' not in columns:
            connection.execute('ALTER TABLE orchestrator_execution_meta ADD COLUMN stderr_path TEXT NULL')

    def set_project_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO project_meta (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, value),
            )
            connection.commit()

    def get_project_meta(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute('SELECT value FROM project_meta WHERE key = ?', (key,)).fetchone()
        return None if row is None else str(row['value'])

    def list_project_meta(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute('SELECT key, value FROM project_meta ORDER BY key').fetchall()
        return {str(row['key']): str(row['value']) for row in rows}

    def latest_run_started_at(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT started_at FROM run_records WHERE started_at IS NOT NULL ORDER BY started_at DESC LIMIT 1'
            ).fetchone()
        return None if row is None else str(row['started_at'])

    def latest_run_finished_at(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT ended_at FROM run_records WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1'
            ).fetchone()
        return None if row is None else str(row['ended_at'])

    def save_notebook_revision(
        self, node_id: str, source_hash: str, docs: str | None, interface_json: dict[str, Any]
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT OR REPLACE INTO notebook_revisions '
                '(node_id, source_hash, saved_at, doc_excerpt, interface_json) VALUES (?, ?, ?, ?, ?)',
                (node_id, source_hash, utc_now_iso(), docs, json_dumps(interface_json)),
            )
            connection.commit()

    def latest_interface_json(self, node_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT interface_json FROM notebook_revisions WHERE node_id = ? ORDER BY rowid DESC LIMIT 1',
                (node_id,),
            ).fetchone()
        return None if row is None else json.loads(str(row['interface_json']))

    def latest_source_hash(self, node_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT source_hash FROM notebook_revisions WHERE node_id = ? ORDER BY rowid DESC LIMIT 1',
                (node_id,),
            ).fetchone()
        return None if row is None else str(row['source_hash'])

    def replace_validation_issues(self, node_id: str, issues: Iterable[ValidationIssue]) -> None:
        with self._connect() as connection:
            connection.execute('DELETE FROM validation_issues WHERE node_id = ?', (node_id,))
            now = utc_now_iso()
            connection.executemany(
                'INSERT INTO validation_issues '
                '(issue_id, node_id, severity, code, message, details_json, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    (
                        issue.issue_id,
                        issue.node_id,
                        issue.severity.value,
                        issue.code,
                        issue.message,
                        json_dumps(issue.details),
                        now,
                    )
                    for issue in issues
                ],
            )
            self._prune_stale_validation_issue_dismissals(connection)
            connection.commit()

    def list_validation_issues(
        self, *, node_id: str | None = None, include_dismissed: bool = False
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            query = (
                'SELECT vi.*, vid.dismissed_at '
                'FROM validation_issues vi '
                'LEFT JOIN validation_issue_dismissals vid ON vid.issue_id = vi.issue_id'
            )
            clauses: list[str] = []
            params: list[Any] = []
            if node_id is not None:
                clauses.append('vi.node_id = ?')
                params.append(node_id)
            if not include_dismissed:
                clauses.append('vid.dismissed_at IS NULL')
            if clauses:
                query = f'{query} WHERE {" AND ".join(clauses)}'
            query = f'{query} ORDER BY vi.node_id, vi.severity DESC, vi.code'
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_validation_issue(row) for row in rows]

    def get_validation_issue(self, issue_id: str, *, include_dismissed: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            query = (
                'SELECT vi.*, vid.dismissed_at '
                'FROM validation_issues vi '
                'LEFT JOIN validation_issue_dismissals vid ON vid.issue_id = vi.issue_id '
                'WHERE vi.issue_id = ?'
            )
            params: list[Any] = [issue_id]
            if not include_dismissed:
                query = f'{query} AND vid.dismissed_at IS NULL'
            row = connection.execute(query, params).fetchone()
        return None if row is None else self._row_to_validation_issue(row)

    def dismiss_validation_issue(self, issue_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO validation_issue_dismissals (issue_id, dismissed_at) VALUES (?, ?) '
                'ON CONFLICT(issue_id) DO UPDATE SET dismissed_at = excluded.dismissed_at',
                (issue_id, utc_now_iso()),
            )
            connection.commit()

    def save_persistent_notice(
        self,
        *,
        issue_id: str,
        node_id: str | None,
        severity: ValidationSeverity,
        code: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO persistent_notices '
                '(issue_id, node_id, severity, code, message, details_json, created_at, dismissed_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, NULL) '
                'ON CONFLICT(issue_id) DO UPDATE SET '
                'node_id = excluded.node_id, '
                'severity = excluded.severity, '
                'code = excluded.code, '
                'message = excluded.message, '
                'details_json = excluded.details_json, '
                'created_at = excluded.created_at, '
                'dismissed_at = NULL',
                (issue_id, node_id, severity.value, code, message, json_dumps(details), now),
            )
            connection.commit()

    def list_persistent_notices(self, *, include_dismissed: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            query = 'SELECT * FROM persistent_notices'
            if not include_dismissed:
                query = f'{query} WHERE dismissed_at IS NULL'
            query = f'{query} ORDER BY created_at DESC, issue_id DESC'
            rows = connection.execute(query).fetchall()
        return [self._row_to_validation_issue(row) for row in rows]

    def get_persistent_notice(self, issue_id: str, *, include_dismissed: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            query = 'SELECT * FROM persistent_notices WHERE issue_id = ?'
            params: list[Any] = [issue_id]
            if not include_dismissed:
                query = f'{query} AND dismissed_at IS NULL'
            row = connection.execute(query, params).fetchone()
        return None if row is None else self._row_to_validation_issue(row)

    def dismiss_persistent_notice(self, issue_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE persistent_notices SET dismissed_at = ? WHERE issue_id = ?',
                (utc_now_iso(), issue_id),
            )
            connection.commit()

    def list_state_node_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT node_id FROM notebook_revisions '
                'UNION SELECT node_id FROM validation_issues '
                'UNION SELECT node_id FROM (SELECT node_id FROM persistent_notices WHERE node_id IS NOT NULL) '
                'UNION SELECT node_id FROM artifact_versions '
                'UNION SELECT node_id FROM artifact_heads '
                'UNION SELECT node_id FROM cache_index '
                'UNION SELECT node_id FROM run_outputs '
                'ORDER BY node_id'
            ).fetchall()
        return [str(row['node_id']) for row in rows]

    def ensure_artifact_head(
        self, node_id: str, artifact_name: str, state: ArtifactState = ArtifactState.PENDING
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO artifact_heads (node_id, artifact_name, current_version_id, state) VALUES (?, ?, NULL, ?)',
                (node_id, artifact_name, state.value),
            )
            connection.commit()

    def delete_artifact_head(self, node_id: str, artifact_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'DELETE FROM artifact_heads WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.commit()

    def delete_node_state(self, node_id: str) -> None:
        with self._connect() as connection:
            connection.execute('DELETE FROM run_inputs WHERE logical_artifact_id LIKE ?', (f'{node_id}/%',))
            connection.execute('DELETE FROM run_outputs WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM cache_index WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM artifact_heads WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM artifact_versions WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM validation_issues WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM persistent_notices WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM notebook_revisions WHERE node_id = ?', (node_id,))
            self._prune_stale_validation_issue_dismissals(connection)
            connection.commit()

    def delete_artifact_state(self, node_id: str, artifact_name: str) -> None:
        with self._connect() as connection:
            connection.execute('DELETE FROM run_inputs WHERE logical_artifact_id = ?', (f'{node_id}/{artifact_name}',))
            connection.execute(
                'DELETE FROM run_outputs WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.execute(
                'DELETE FROM cache_index WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.execute(
                'DELETE FROM artifact_heads WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.execute(
                'DELETE FROM artifact_versions WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.commit()

    def set_artifact_head_state(self, node_id: str, artifact_name: str, state: ArtifactState) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE artifact_heads SET state = ? WHERE node_id = ? AND artifact_name = ?',
                (state.value, node_id, artifact_name),
            )
            connection.commit()

    def upsert_artifact_object(
        self,
        artifact_hash: str,
        storage_kind: str,
        data_type: str,
        size_bytes: int,
        extension: str | None,
        mime_type: str | None,
        preview_json: dict[str, Any] | None,
    ) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO artifact_objects '
                '(artifact_hash, storage_kind, data_type, size_bytes, extension, mime_type, preview_json, created_at, '
                'last_accessed_at, nondeterministic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)',
                (
                    artifact_hash,
                    storage_kind,
                    data_type,
                    size_bytes,
                    extension,
                    mime_type,
                    None if preview_json is None else json_dumps(preview_json),
                    now,
                    now,
                ),
            )
            connection.commit()

    def touch_artifact_object(self, artifact_hash: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE artifact_objects SET last_accessed_at = ? WHERE artifact_hash = ?',
                (utc_now_iso(), artifact_hash),
            )
            connection.commit()

    def create_artifact_version(
        self,
        *,
        node_id: str,
        artifact_name: str,
        role: ArtifactRole,
        artifact_hash: str,
        source_hash: str,
        upstream_code_hash: str,
        upstream_data_hash: str,
        run_id: str,
        lineage_mode: LineageMode,
        warnings: list[dict[str, Any]],
        state: ArtifactState = ArtifactState.READY,
    ) -> int:
        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                'INSERT INTO artifact_versions '
                '(node_id, artifact_name, role, artifact_hash, source_hash, upstream_code_hash, upstream_data_hash, '
                'run_id, lineage_mode, created_at, warning_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    node_id,
                    artifact_name,
                    role.value,
                    artifact_hash,
                    source_hash,
                    upstream_code_hash,
                    upstream_data_hash,
                    run_id,
                    lineage_mode.value,
                    now,
                    json_dumps(warnings),
                ),
            )
            last_row_id = cursor.lastrowid
            if last_row_id is None:
                raise RuntimeError('Failed to create artifact version.')
            version_id = int(last_row_id)
            connection.execute(
                'INSERT INTO artifact_heads (node_id, artifact_name, current_version_id, state) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(node_id, artifact_name) DO UPDATE SET current_version_id = excluded.current_version_id, '
                'state = excluded.state',
                (node_id, artifact_name, version_id, state.value),
            )
            existing = connection.execute(
                'SELECT artifact_hash FROM cache_index WHERE node_id = ? AND artifact_name = ? AND upstream_data_hash = ?',
                (node_id, artifact_name, upstream_data_hash),
            ).fetchone()
            is_nondeterministic = 1 if existing and existing['artifact_hash'] != artifact_hash else 0
            connection.execute(
                'INSERT INTO cache_index (node_id, artifact_name, upstream_data_hash, artifact_hash, is_nondeterministic, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(node_id, artifact_name, upstream_data_hash) DO UPDATE SET '
                'artifact_hash = excluded.artifact_hash, is_nondeterministic = MAX(cache_index.is_nondeterministic, excluded.is_nondeterministic), '
                'updated_at = excluded.updated_at',
                (node_id, artifact_name, upstream_data_hash, artifact_hash, is_nondeterministic, now),
            )
            connection.execute(
                'INSERT INTO run_outputs (run_id, node_id, artifact_name, version_id) VALUES (?, ?, ?, ?)',
                (run_id, node_id, artifact_name, version_id),
            )
            connection.commit()
            return version_id

    def get_artifact_head(self, node_id: str, artifact_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT ah.node_id, ah.artifact_name, ah.current_version_id, ah.state, '
                'av.role, av.artifact_hash, av.source_hash, av.upstream_code_hash, av.upstream_data_hash, '
                'av.run_id, av.lineage_mode, av.created_at, av.warning_json, ao.storage_kind, ao.data_type, '
                'ao.size_bytes, ao.extension, ao.mime_type, ao.preview_json '
                'FROM artifact_heads ah '
                'LEFT JOIN artifact_versions av ON av.version_id = ah.current_version_id '
                'LEFT JOIN artifact_objects ao ON ao.artifact_hash = av.artifact_hash '
                'WHERE ah.node_id = ? AND ah.artifact_name = ?',
                (node_id, artifact_name),
            ).fetchone()
        return None if row is None else self._row_to_artifact(row)

    def list_artifact_heads(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT ah.node_id, ah.artifact_name, ah.current_version_id, ah.state, '
                'av.role, av.artifact_hash, av.source_hash, av.upstream_code_hash, av.upstream_data_hash, '
                'av.run_id, av.lineage_mode, av.created_at, av.warning_json, ao.storage_kind, ao.data_type, '
                'ao.size_bytes, ao.extension, ao.mime_type, ao.preview_json '
                'FROM artifact_heads ah '
                'LEFT JOIN artifact_versions av ON av.version_id = ah.current_version_id '
                'LEFT JOIN artifact_objects ao ON ao.artifact_hash = av.artifact_hash '
                'ORDER BY ah.node_id, ah.artifact_name'
            ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def record_run(
        self,
        run_id: str,
        project_id: str,
        mode: str,
        target_json: dict[str, Any],
        graph_version: int,
        source_snapshot_json: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO run_records '
                '(run_id, project_id, mode, status, target_json, graph_version, source_snapshot_json, started_at, ended_at, failure_json) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)',
                (
                    run_id,
                    project_id,
                    mode,
                    RunStatus.QUEUED.value,
                    json_dumps(target_json),
                    graph_version,
                    json_dumps(source_snapshot_json),
                ),
            )
            connection.commit()

    def update_run_status(self, run_id: str, status: RunStatus, *, failure_json: dict[str, Any] | None = None) -> None:
        with self._connect() as connection:
            started_at = utc_now_iso() if status == RunStatus.RUNNING else None
            ended_at = (
                utc_now_iso()
                if status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ABORTED_ON_RESTART}
                else None
            )
            if started_at is not None:
                connection.execute(
                    'UPDATE run_records SET status = ?, started_at = ? WHERE run_id = ?',
                    (status.value, started_at, run_id),
                )
            elif ended_at is not None:
                connection.execute(
                    'UPDATE run_records SET status = ?, ended_at = ?, failure_json = COALESCE(?, failure_json) WHERE run_id = ?',
                    (status.value, ended_at, None if failure_json is None else json_dumps(failure_json), run_id),
                )
            else:
                connection.execute('UPDATE run_records SET status = ? WHERE run_id = ?', (status.value, run_id))
            connection.commit()

    def record_run_input(
        self, run_id: str, logical_artifact_id: str, artifact_hash_at_load: str, state_at_load: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO run_inputs (run_id, logical_artifact_id, artifact_hash_at_load, state_at_load, loaded_at) VALUES (?, ?, ?, ?, ?)',
                (run_id, logical_artifact_id, artifact_hash_at_load, state_at_load, utc_now_iso()),
            )
            connection.commit()

    def get_cache_hit(self, node_id: str, artifact_name: str, upstream_data_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT artifact_hash, is_nondeterministic FROM cache_index WHERE node_id = ? AND artifact_name = ? AND upstream_data_hash = ?',
                (node_id, artifact_name, upstream_data_hash),
            ).fetchone()
        if row is None:
            return None
        return {'artifact_hash': row['artifact_hash'], 'is_nondeterministic': bool(row['is_nondeterministic'])}

    def list_run_records(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT * FROM run_records ORDER BY COALESCE(started_at, ended_at) DESC, run_id DESC'
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record['target_json'] = json.loads(str(record['target_json']))
            record['source_snapshot_json'] = json.loads(str(record['source_snapshot_json']))
            if record['failure_json']:
                record['failure_json'] = json.loads(str(record['failure_json']))
            records.append(record)
        return records

    def upsert_orchestrator_execution_meta(
        self,
        *,
        node_id: str,
        run_id: str,
        status: str,
        started_at: str,
        ended_at: str | None = None,
        duration_seconds: float | None = None,
        current_cell: dict[str, Any] | None = None,
        total_cells: int | None = None,
        last_completed_cell_number: int | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO orchestrator_execution_meta '
                '(node_id, run_id, status, started_at, ended_at, duration_seconds, current_cell_json, total_cells, last_completed_cell_number, stdout_path, stderr_path, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(node_id) DO UPDATE SET '
                'run_id = excluded.run_id, '
                'status = excluded.status, '
                'started_at = excluded.started_at, '
                'ended_at = excluded.ended_at, '
                'duration_seconds = excluded.duration_seconds, '
                'current_cell_json = excluded.current_cell_json, '
                'total_cells = excluded.total_cells, '
                'last_completed_cell_number = excluded.last_completed_cell_number, '
                'stdout_path = excluded.stdout_path, '
                'stderr_path = excluded.stderr_path, '
                'updated_at = excluded.updated_at',
                (
                    node_id,
                    run_id,
                    status,
                    started_at,
                    ended_at,
                    duration_seconds,
                    None if current_cell is None else json_dumps(current_cell),
                    total_cells,
                    last_completed_cell_number,
                    stdout_path,
                    stderr_path,
                    utc_now_iso(),
                ),
            )
            connection.commit()

    def list_orchestrator_execution_meta(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT * FROM orchestrator_execution_meta ORDER BY updated_at DESC, node_id ASC'
            ).fetchall()
        records: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = dict(row)
            if record['current_cell_json']:
                record['current_cell'] = json.loads(str(record['current_cell_json']))
            else:
                record['current_cell'] = None
            del record['current_cell_json']
            stdout_path = record.pop('stdout_path', None)
            stderr_path = record.pop('stderr_path', None)
            record['stdout'] = _read_optional_text_file_summary(stdout_path)
            record['stderr'] = _read_optional_text_file_summary(stderr_path)
            records[str(record['node_id'])] = record
        return records

    def abort_inflight_runs(self) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE run_records SET status = ?, ended_at = ? WHERE status IN (?, ?)',
                (RunStatus.ABORTED_ON_RESTART.value, utc_now_iso(), RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            )
            connection.commit()

    def create_checkpoint(self, checkpoint_id: str, graph_version: int, path: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'INSERT INTO checkpoints (checkpoint_id, created_at, graph_version, path, restored_at) VALUES (?, ?, ?, ?, NULL)',
                (checkpoint_id, utc_now_iso(), graph_version, path),
            )
            connection.commit()

    def mark_checkpoint_restored(self, checkpoint_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE checkpoints SET restored_at = ? WHERE checkpoint_id = ?', (utc_now_iso(), checkpoint_id)
            )
            connection.commit()

    def list_checkpoints(self) -> list[CheckpointRecord]:
        with self._connect() as connection:
            rows = connection.execute('SELECT * FROM checkpoints ORDER BY created_at DESC').fetchall()
        return [CheckpointRecord(**dict(row)) for row in rows]

    @staticmethod
    def _row_to_validation_issue(row: sqlite3.Row) -> dict[str, Any]:
        details_raw = row['details_json']
        return {
            'issue_id': row['issue_id'],
            'node_id': row['node_id'],
            'severity': row['severity'],
            'code': row['code'],
            'message': row['message'],
            'details': {} if details_raw is None else json.loads(str(details_raw)),
            'created_at': row['created_at'],
        }

    @staticmethod
    def _prune_stale_validation_issue_dismissals(connection: sqlite3.Connection) -> None:
        connection.execute(
            'DELETE FROM validation_issue_dismissals WHERE issue_id NOT IN (SELECT issue_id FROM validation_issues)'
        )

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> dict[str, Any]:
        return {
            'node_id': row['node_id'],
            'artifact_name': row['artifact_name'],
            'current_version_id': row['current_version_id'],
            'state': row['state'],
            'role': row['role'],
            'artifact_hash': row['artifact_hash'],
            'source_hash': row['source_hash'],
            'upstream_code_hash': row['upstream_code_hash'],
            'upstream_data_hash': row['upstream_data_hash'],
            'run_id': row['run_id'],
            'lineage_mode': row['lineage_mode'],
            'created_at': row['created_at'],
            'warnings': [] if row['warning_json'] is None else json.loads(str(row['warning_json'])),
            'storage_kind': row['storage_kind'],
            'data_type': row['data_type'],
            'size_bytes': row['size_bytes'],
            'extension': row['extension'],
            'mime_type': row['mime_type'],
            'preview': None if row['preview_json'] is None else json.loads(str(row['preview_json'])),
        }


def _read_optional_text_file(path_value: object) -> str | None:
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return None


def _read_optional_text_file_summary(path_value: object) -> dict[str, Any] | None:
    content = _read_optional_text_file(path_value)
    if content is None:
        return None
    lines = content.splitlines()
    preview = '\n'.join(lines[:LOG_PREVIEW_MAX_LINES])
    truncated = len(lines) > LOG_PREVIEW_MAX_LINES
    if len(preview) > LOG_PREVIEW_MAX_CHARS:
        preview = preview[:LOG_PREVIEW_MAX_CHARS]
        truncated = True
    if content.endswith('\n') and preview and not preview.endswith('\n') and not truncated:
        preview = f'{preview}\n'
    return {'text': preview, 'truncated': truncated}
