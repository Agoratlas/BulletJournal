from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from bulletjournal.config import DB_TIMEOUT_SECONDS
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, RunStatus, ValidationSeverity
from bulletjournal.domain.models import AssetDeclaration, CheckpointRecord, ValidationIssue
from bulletjournal.storage.migrations import MIGRATIONS
from bulletjournal.utils import json_dumps, utc_now_iso

LOG_PREVIEW_MAX_CHARS = 10_000
LOG_PREVIEW_TAIL_READ_BYTES = LOG_PREVIEW_MAX_CHARS * 4
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

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection, connection:
            yield connection

    def _initialize(self) -> None:
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO project_meta (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, value),
            )
            connection.commit()

    def get_project_meta(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute('SELECT value FROM project_meta WHERE key = ?', (key,)).fetchone()
        return None if row is None else str(row['value'])

    def list_project_meta(self) -> dict[str, str]:
        with self._connection() as connection:
            rows = connection.execute('SELECT key, value FROM project_meta ORDER BY key').fetchall()
        return {str(row['key']): str(row['value']) for row in rows}

    def latest_run_started_at(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT started_at FROM run_records WHERE started_at IS NOT NULL ORDER BY started_at DESC LIMIT 1'
            ).fetchone()
        return None if row is None else str(row['started_at'])

    def latest_run_finished_at(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT ended_at FROM run_records WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1'
            ).fetchone()
        return None if row is None else str(row['ended_at'])

    def save_notebook_contract(
        self, node_id: str, source_hash: str, docs: str | None, contract_json: dict[str, Any]
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT OR REPLACE INTO notebook_revisions '
                '(node_id, source_hash, saved_at, doc_excerpt, interface_json) VALUES (?, ?, ?, ?, ?)',
                (node_id, source_hash, utc_now_iso(), docs, json_dumps(contract_json)),
            )
            connection.commit()

    def save_notebook_revision(
        self, node_id: str, source_hash: str, docs: str | None, interface_json: dict[str, Any]
    ) -> None:
        self.save_notebook_contract(node_id, source_hash, docs, interface_json)

    def latest_notebook_contract_json(self, node_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT interface_json FROM notebook_revisions WHERE node_id = ? ORDER BY rowid DESC LIMIT 1',
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row['interface_json']))
        return payload if isinstance(payload, dict) else None

    def latest_interface_json(self, node_id: str) -> dict[str, Any] | None:
        payload = self.latest_notebook_contract_json(node_id)
        if payload is None:
            return None
        interface = payload.get('interface')
        return dict(interface) if isinstance(interface, dict) else payload

    def latest_source_hash(self, node_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT source_hash FROM notebook_revisions WHERE node_id = ? ORDER BY rowid DESC LIMIT 1',
                (node_id,),
            ).fetchone()
        return None if row is None else str(row['source_hash'])

    def replace_validation_issues(self, node_id: str, issues: Iterable[ValidationIssue]) -> None:
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
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
                'created_at = excluded.created_at',
                (issue_id, node_id, severity.value, code, message, json_dumps(details), now),
            )
            connection.commit()

    def list_persistent_notices(self, *, include_dismissed: bool = False) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = 'SELECT * FROM persistent_notices'
            if not include_dismissed:
                query = f'{query} WHERE dismissed_at IS NULL'
            query = f'{query} ORDER BY created_at DESC, issue_id DESC'
            rows = connection.execute(query).fetchall()
        return [self._row_to_validation_issue(row) for row in rows]

    def get_persistent_notice(self, issue_id: str, *, include_dismissed: bool = True) -> dict[str, Any] | None:
        with self._connection() as connection:
            query = 'SELECT * FROM persistent_notices WHERE issue_id = ?'
            params: list[Any] = [issue_id]
            if not include_dismissed:
                query = f'{query} AND dismissed_at IS NULL'
            row = connection.execute(query, params).fetchone()
        return None if row is None else self._row_to_validation_issue(row)

    def dismiss_persistent_notice(self, issue_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'UPDATE persistent_notices SET dismissed_at = ? WHERE issue_id = ?',
                (utc_now_iso(), issue_id),
            )
            connection.commit()

    def dismiss_persistent_notices_for_node(self, node_id: str, codes: list[str]) -> None:
        if not codes:
            return
        now = utc_now_iso()
        with self._connection() as connection:
            connection.executemany(
                'UPDATE persistent_notices SET dismissed_at = ? WHERE node_id = ? AND code = ?',
                [(now, node_id, code) for code in codes],
            )
            connection.commit()

    def list_state_node_ids(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT node_id FROM notebook_revisions '
                'UNION SELECT node_id FROM validation_issues '
                'UNION SELECT node_id FROM (SELECT node_id FROM persistent_notices WHERE node_id IS NOT NULL) '
                'UNION SELECT node_id FROM artifact_versions '
                'UNION SELECT node_id FROM artifact_heads '
                'UNION SELECT node_id FROM asset_declarations '
                'UNION SELECT node_id FROM asset_versions '
                'UNION SELECT node_id FROM asset_heads '
                'UNION SELECT node_id FROM cache_index '
                'UNION SELECT node_id FROM orchestrator_execution_meta '
                'UNION SELECT node_id FROM run_outputs '
                'ORDER BY node_id'
            ).fetchall()
        return [str(row['node_id']) for row in rows]

    def replace_asset_declarations(
        self,
        node_id: str,
        source_hash: str,
        declarations: Iterable[AssetDeclaration],
    ) -> None:
        with self._connection() as connection:
            connection.execute('DELETE FROM asset_declarations WHERE node_id = ?', (node_id,))
            now = utc_now_iso()
            connection.executemany(
                'INSERT INTO asset_declarations '
                '('
                'node_id, asset_name, title, description, declared_asset_type, declaration_index, '
                'source_hash, updated_at'
                ') '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    (
                        declaration.node_id,
                        declaration.name,
                        declaration.title,
                        declaration.description,
                        declaration.declared_asset_type,
                        declaration.declaration_index,
                        source_hash,
                        now,
                    )
                    for declaration in declarations
                ],
            )
            connection.commit()

    def list_asset_declarations(self, node_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = (
                'SELECT node_id, asset_name, title, description, declared_asset_type, declaration_index, '
                'source_hash, updated_at FROM asset_declarations'
            )
            params: list[Any] = []
            if node_id is not None:
                query = f'{query} WHERE node_id = ?'
                params.append(node_id)
            query = f'{query} ORDER BY node_id, declaration_index, asset_name'
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_asset_declaration(row) for row in rows]

    def ensure_asset_head(self, node_id: str, asset_name: str, state: ArtifactState = ArtifactState.PENDING) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO asset_heads '
                '(node_id, asset_name, current_asset_version_id, state) VALUES (?, ?, NULL, ?)',
                (node_id, asset_name, state.value),
            )
            connection.commit()

    def delete_asset_state(self, node_id: str, asset_name: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'DELETE FROM asset_version_objects WHERE asset_version_id IN '
                '(SELECT asset_version_id FROM asset_versions WHERE node_id = ? AND asset_name = ?)',
                (node_id, asset_name),
            )
            connection.execute(
                'DELETE FROM asset_heads WHERE node_id = ? AND asset_name = ?',
                (node_id, asset_name),
            )
            connection.execute(
                'DELETE FROM asset_declarations WHERE node_id = ? AND asset_name = ?',
                (node_id, asset_name),
            )
            connection.execute(
                'DELETE FROM asset_versions WHERE node_id = ? AND asset_name = ?',
                (node_id, asset_name),
            )
            connection.commit()

    def set_asset_head_state(self, node_id: str, asset_name: str, state: ArtifactState) -> None:
        with self._connection() as connection:
            connection.execute(
                'UPDATE asset_heads SET state = ? WHERE node_id = ? AND asset_name = ?',
                (state.value, node_id, asset_name),
            )
            connection.commit()

    def create_asset_version(
        self,
        *,
        node_id: str,
        asset_name: str,
        asset_type: str,
        interactive: bool,
        source_hash: str,
        upstream_code_hash: str,
        upstream_data_hash: str,
        run_id: str,
        lineage_mode: LineageMode,
        definition: dict[str, Any],
        modifier_schema: list[dict[str, Any]],
        default_modifiers: dict[str, Any],
        override_schema_hash: str,
        warnings: list[dict[str, Any]],
        objects: list[dict[str, Any]],
        state: ArtifactState = ArtifactState.READY,
    ) -> int:
        now = utc_now_iso()
        with self._connection() as connection:
            cursor = connection.execute(
                'INSERT INTO asset_versions '
                '(node_id, asset_name, asset_type, interactive, source_hash, upstream_code_hash, upstream_data_hash, '
                'run_id, lineage_mode, definition_json, modifier_schema_json, default_modifiers_json, '
                'override_schema_hash, warning_json, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    node_id,
                    asset_name,
                    asset_type,
                    1 if interactive else 0,
                    source_hash,
                    upstream_code_hash,
                    upstream_data_hash,
                    run_id,
                    lineage_mode.value,
                    json_dumps(definition),
                    json_dumps(modifier_schema),
                    json_dumps(default_modifiers),
                    override_schema_hash,
                    json_dumps(warnings),
                    now,
                ),
            )
            last_row_id = cursor.lastrowid
            if last_row_id is None:
                raise RuntimeError('Failed to create asset version.')
            asset_version_id = int(last_row_id)
            if objects:
                connection.executemany(
                    'INSERT INTO asset_version_objects '
                    '(asset_version_id, object_role, object_index, artifact_hash, metadata_json) '
                    'VALUES (?, ?, ?, ?, ?)',
                    [
                        (
                            asset_version_id,
                            item['object_role'],
                            int(item.get('object_index', 0)),
                            item['artifact_hash'],
                            None if item.get('metadata') is None else json_dumps(item['metadata']),
                        )
                        for item in objects
                    ],
                )
            connection.execute(
                'INSERT INTO asset_heads (node_id, asset_name, current_asset_version_id, state) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(node_id, asset_name) DO UPDATE SET '
                'current_asset_version_id = excluded.current_asset_version_id, state = excluded.state',
                (node_id, asset_name, asset_version_id, state.value),
            )
            connection.commit()
            return asset_version_id

    def get_asset_head(self, node_id: str, asset_name: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT ah.node_id, ah.asset_name, ah.current_asset_version_id, ah.state, '
                'ad.title, ad.description, ad.declared_asset_type, ad.declaration_index, '
                'ad.source_hash AS declaration_source_hash, av.asset_version_id, av.asset_type, av.interactive, '
                'av.source_hash, av.upstream_code_hash, av.upstream_data_hash, av.run_id, av.lineage_mode, '
                'av.definition_json, av.modifier_schema_json, av.default_modifiers_json, '
                'av.override_schema_hash, av.warning_json, av.created_at '
                'FROM asset_heads ah '
                'LEFT JOIN asset_declarations ad ON ad.node_id = ah.node_id AND ad.asset_name = ah.asset_name '
                'LEFT JOIN asset_versions av ON av.asset_version_id = ah.current_asset_version_id '
                'WHERE ah.node_id = ? AND ah.asset_name = ?',
                (node_id, asset_name),
            ).fetchone()
            if row is None:
                return None
            version_id = row['current_asset_version_id']
            objects = (
                self._asset_objects_for_version_ids(connection, [int(version_id)]) if version_id is not None else {}
            )
        return self._row_to_asset(row, objects.get(int(version_id), []) if version_id is not None else [])

    def list_asset_heads(self, *, node_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = (
                'SELECT ah.node_id, ah.asset_name, ah.current_asset_version_id, ah.state, '
                'ad.title, ad.description, ad.declared_asset_type, ad.declaration_index, '
                'ad.source_hash AS declaration_source_hash, av.asset_version_id, av.asset_type, av.interactive, '
                'av.source_hash, av.upstream_code_hash, av.upstream_data_hash, av.run_id, av.lineage_mode, '
                'av.definition_json, av.modifier_schema_json, av.default_modifiers_json, '
                'av.override_schema_hash, av.warning_json, av.created_at '
                'FROM asset_heads ah '
                'LEFT JOIN asset_declarations ad ON ad.node_id = ah.node_id AND ad.asset_name = ah.asset_name '
                'LEFT JOIN asset_versions av ON av.asset_version_id = ah.current_asset_version_id'
            )
            params: list[Any] = []
            if node_id is not None:
                query = f'{query} WHERE ah.node_id = ?'
                params.append(node_id)
            query = f'{query} ORDER BY ah.node_id, COALESCE(ad.declaration_index, 2147483647), ah.asset_name'
            rows = connection.execute(query, params).fetchall()
            version_ids = [
                int(row['current_asset_version_id']) for row in rows if row['current_asset_version_id'] is not None
            ]
            objects_by_version_id = self._asset_objects_for_version_ids(connection, version_ids)
        return [
            self._row_to_asset(
                row,
                objects_by_version_id.get(int(row['current_asset_version_id']), [])
                if row['current_asset_version_id'] is not None
                else [],
            )
            for row in rows
        ]

    def ensure_artifact_head(
        self, node_id: str, artifact_name: str, state: ArtifactState = ArtifactState.PENDING
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO artifact_heads '
                '(node_id, artifact_name, current_version_id, state) VALUES (?, ?, NULL, ?)',
                (node_id, artifact_name, state.value),
            )
            connection.commit()

    def delete_artifact_head(self, node_id: str, artifact_name: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'DELETE FROM artifact_heads WHERE node_id = ? AND artifact_name = ?',
                (node_id, artifact_name),
            )
            connection.commit()

    def delete_node_state(self, node_id: str) -> None:
        with self._connection() as connection:
            self._delete_run_records_for_node(connection, node_id)
            connection.execute('DELETE FROM run_inputs WHERE logical_artifact_id LIKE ?', (f'{node_id}/%',))
            connection.execute('DELETE FROM run_outputs WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM cache_index WHERE node_id = ?', (node_id,))
            connection.execute(
                'DELETE FROM asset_version_objects WHERE asset_version_id IN '
                '(SELECT asset_version_id FROM asset_versions WHERE node_id = ?)',
                (node_id,),
            )
            connection.execute('DELETE FROM asset_heads WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM asset_declarations WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM asset_versions WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM artifact_heads WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM artifact_versions WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM orchestrator_execution_meta WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM validation_issues WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM persistent_notices WHERE node_id = ?', (node_id,))
            connection.execute('DELETE FROM notebook_revisions WHERE node_id = ?', (node_id,))
            self._prune_stale_validation_issue_dismissals(connection)
            connection.commit()

    def rename_node_state(self, old_node_id: str, new_node_id: str) -> None:
        if old_node_id == new_node_id:
            return
        with self._connection() as connection:
            connection.execute(
                'UPDATE notebook_revisions SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id)
            )
            connection.execute('UPDATE validation_issues SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute(
                'UPDATE persistent_notices SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id)
            )
            connection.execute(
                'UPDATE asset_declarations SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id)
            )
            connection.execute('UPDATE asset_versions SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute('UPDATE asset_heads SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute('UPDATE artifact_versions SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute('UPDATE artifact_heads SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute('UPDATE cache_index SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute(
                'UPDATE orchestrator_execution_meta SET node_id = ? WHERE node_id = ?',
                (new_node_id, old_node_id),
            )
            connection.execute('UPDATE run_outputs SET node_id = ? WHERE node_id = ?', (new_node_id, old_node_id))
            connection.execute(
                'UPDATE run_inputs SET logical_artifact_id = ? || substr(logical_artifact_id, ?) '
                'WHERE logical_artifact_id LIKE ?',
                (new_node_id, len(old_node_id) + 1, f'{old_node_id}/%'),
            )
            self._rename_issue_detail_refs(connection, 'validation_issues', old_node_id, new_node_id)
            self._rename_issue_detail_refs(connection, 'persistent_notices', old_node_id, new_node_id)
            self._rename_run_record_node_refs(connection, old_node_id, new_node_id)
            self._prune_stale_validation_issue_dismissals(connection)
            connection.commit()

    def delete_artifact_state(self, node_id: str, artifact_name: str) -> None:
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO objects '
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
        with self._connection() as connection:
            connection.execute(
                'UPDATE objects SET last_accessed_at = ? WHERE artifact_hash = ?',
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
        with self._connection() as connection:
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
                'SELECT artifact_hash FROM cache_index '
                'WHERE node_id = ? AND artifact_name = ? AND upstream_data_hash = ?',
                (node_id, artifact_name, upstream_data_hash),
            ).fetchone()
            is_nondeterministic = 1 if existing and existing['artifact_hash'] != artifact_hash else 0
            connection.execute(
                'INSERT INTO cache_index '
                '(node_id, artifact_name, upstream_data_hash, artifact_hash, is_nondeterministic, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(node_id, artifact_name, upstream_data_hash) DO UPDATE SET '
                'artifact_hash = excluded.artifact_hash, '
                'is_nondeterministic = MAX(cache_index.is_nondeterministic, excluded.is_nondeterministic), '
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
        with self._connection() as connection:
            row = connection.execute(
                'SELECT ah.node_id, ah.artifact_name, ah.current_version_id, ah.state, '
                'av.role, av.artifact_hash, av.source_hash, av.upstream_code_hash, av.upstream_data_hash, '
                'av.run_id, av.lineage_mode, av.created_at, av.warning_json, ao.storage_kind, ao.data_type, '
                'ao.size_bytes, ao.extension, ao.mime_type, ao.preview_json '
                'FROM artifact_heads ah '
                'LEFT JOIN artifact_versions av ON av.version_id = ah.current_version_id '
                'LEFT JOIN objects ao ON ao.artifact_hash = av.artifact_hash '
                'WHERE ah.node_id = ? AND ah.artifact_name = ?',
                (node_id, artifact_name),
            ).fetchone()
        return None if row is None else self._row_to_artifact(row)

    def list_artifact_heads(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT ah.node_id, ah.artifact_name, ah.current_version_id, ah.state, '
                'av.role, av.artifact_hash, av.source_hash, av.upstream_code_hash, av.upstream_data_hash, '
                'av.run_id, av.lineage_mode, av.created_at, av.warning_json, ao.storage_kind, ao.data_type, '
                'ao.size_bytes, ao.extension, ao.mime_type, ao.preview_json '
                'FROM artifact_heads ah '
                'LEFT JOIN artifact_versions av ON av.version_id = ah.current_version_id '
                'LEFT JOIN objects ao ON ao.artifact_hash = av.artifact_hash '
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
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO run_records '
                '(run_id, project_id, mode, status, target_json, graph_version, '
                'source_snapshot_json, started_at, ended_at, failure_json) '
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
        with self._connection() as connection:
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
                    'UPDATE run_records SET status = ?, ended_at = ?, '
                    'failure_json = COALESCE(?, failure_json) WHERE run_id = ?',
                    (status.value, ended_at, None if failure_json is None else json_dumps(failure_json), run_id),
                )
            else:
                connection.execute('UPDATE run_records SET status = ? WHERE run_id = ?', (status.value, run_id))
            connection.commit()

    def record_run_input(
        self, run_id: str, logical_artifact_id: str, artifact_hash_at_load: str, state_at_load: str
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO run_inputs '
                '(run_id, logical_artifact_id, artifact_hash_at_load, state_at_load, loaded_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (run_id, logical_artifact_id, artifact_hash_at_load, state_at_load, utc_now_iso()),
            )
            connection.commit()

    def get_cache_hit(self, node_id: str, artifact_name: str, upstream_data_hash: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT artifact_hash, is_nondeterministic FROM cache_index '
                'WHERE node_id = ? AND artifact_name = ? AND upstream_data_hash = ?',
                (node_id, artifact_name, upstream_data_hash),
            ).fetchone()
        if row is None:
            return None
        return {'artifact_hash': row['artifact_hash'], 'is_nondeterministic': bool(row['is_nondeterministic'])}

    def list_run_records(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO orchestrator_execution_meta '
                '(node_id, run_id, status, started_at, ended_at, duration_seconds, '
                'current_cell_json, total_cells, last_completed_cell_number, stdout_path, stderr_path, updated_at) '
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                'UPDATE run_records SET status = ?, ended_at = ? WHERE status IN (?, ?)',
                (RunStatus.ABORTED_ON_RESTART.value, utc_now_iso(), RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            )
            connection.commit()

    def create_checkpoint(self, checkpoint_id: str, graph_version: int, path: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO checkpoints (checkpoint_id, created_at, graph_version, path, restored_at) '
                'VALUES (?, ?, ?, ?, NULL)',
                (checkpoint_id, utc_now_iso(), graph_version, path),
            )
            connection.commit()

    def mark_checkpoint_restored(self, checkpoint_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'UPDATE checkpoints SET restored_at = ? WHERE checkpoint_id = ?', (utc_now_iso(), checkpoint_id)
            )
            connection.commit()

    def list_checkpoints(self) -> list[CheckpointRecord]:
        with self._connection() as connection:
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

    @classmethod
    def _delete_run_records_for_node(cls, connection: sqlite3.Connection, node_id: str) -> None:
        run_ids = [
            str(row['run_id'])
            for row in connection.execute('SELECT run_id, target_json, failure_json FROM run_records').fetchall()
            if cls._run_record_references_node(row, node_id)
        ]
        if not run_ids:
            return
        run_id_rows = [(run_id,) for run_id in run_ids]
        connection.executemany('DELETE FROM run_inputs WHERE run_id = ?', run_id_rows)
        connection.executemany('DELETE FROM run_outputs WHERE run_id = ?', run_id_rows)
        connection.executemany('DELETE FROM run_records WHERE run_id = ?', run_id_rows)

    @classmethod
    def _rename_run_record_node_refs(
        cls,
        connection: sqlite3.Connection,
        old_node_id: str,
        new_node_id: str,
    ) -> None:
        rows = connection.execute(
            'SELECT run_id, target_json, source_snapshot_json, failure_json FROM run_records'
        ).fetchall()
        for row in rows:
            target = cls._load_json_dict(row['target_json'])
            source_snapshot = cls._load_json_dict(row['source_snapshot_json'])
            failure = cls._load_json_dict(row['failure_json'])
            changed = False
            if cls._rename_node_refs_in_json(target, old_node_id, new_node_id):
                changed = True
            if cls._rename_node_refs_in_json(source_snapshot, old_node_id, new_node_id):
                changed = True
            if cls._rename_node_refs_in_json(failure, old_node_id, new_node_id):
                changed = True
            if not changed:
                continue
            connection.execute(
                'UPDATE run_records SET target_json = ?, source_snapshot_json = ?, failure_json = ? WHERE run_id = ?',
                (
                    json_dumps(target or {}),
                    json_dumps(source_snapshot or {}),
                    None if failure is None else json_dumps(failure),
                    row['run_id'],
                ),
            )

    @classmethod
    def _rename_issue_detail_refs(
        cls,
        connection: sqlite3.Connection,
        table_name: str,
        old_node_id: str,
        new_node_id: str,
    ) -> None:
        if table_name == 'validation_issues':
            select_query = 'SELECT issue_id, details_json FROM validation_issues'
            update_query = 'UPDATE validation_issues SET details_json = ? WHERE issue_id = ?'
        elif table_name == 'persistent_notices':
            select_query = 'SELECT issue_id, details_json FROM persistent_notices'
            update_query = 'UPDATE persistent_notices SET details_json = ? WHERE issue_id = ?'
        else:
            raise ValueError(f'Unsupported issue detail table `{table_name}`.')
        rows = connection.execute(select_query).fetchall()
        for row in rows:
            details = cls._load_json_dict(row['details_json'])
            if not cls._rename_node_refs_in_json(details, old_node_id, new_node_id):
                continue
            connection.execute(
                update_query,
                (json_dumps(details or {}), row['issue_id']),
            )

    @classmethod
    def _run_record_references_node(cls, row: sqlite3.Row, node_id: str) -> bool:
        target = cls._load_json_dict(row['target_json'])
        if cls._run_target_references_node(target, node_id):
            return True
        failure = cls._load_json_dict(row['failure_json'])
        return cls._run_failure_references_node(failure, node_id)

    @staticmethod
    def _load_json_dict(raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        decoded = json.loads(str(raw))
        return decoded if isinstance(decoded, dict) else None

    @classmethod
    def _rename_node_refs_in_json(
        cls,
        payload: Any,
        old_node_id: str,
        new_node_id: str,
    ) -> bool:
        if isinstance(payload, dict):
            changed = False
            for key, value in payload.items():
                if key == 'node_id' and value == old_node_id:
                    payload[key] = new_node_id
                    changed = True
                    continue
                if key in {'source_node', 'target_node', 'current_node'} and value == old_node_id:
                    payload[key] = new_node_id
                    changed = True
                    continue
                if key in {'node_ids', 'plan'} and isinstance(value, list):
                    next_values = [new_node_id if item == old_node_id else item for item in value]
                    if next_values != value:
                        payload[key] = next_values
                        changed = True
                    continue
                if (
                    key in {'logical_artifact_id', 'artifact', 'source'}
                    and isinstance(value, str)
                    and value.startswith(f'{old_node_id}/')
                ):
                    payload[key] = f'{new_node_id}{value[len(old_node_id) :]}'
                    changed = True
                    continue
                if key == 'id' and cls._dict_represents_node_payload(payload) and value == old_node_id:
                    payload[key] = new_node_id
                    changed = True
                    continue
                if cls._rename_node_refs_in_json(value, old_node_id, new_node_id):
                    changed = True
            if cls._dict_represents_edge_payload(payload):
                next_edge_id = (
                    f'{payload["source_node"]}.{payload["source_port"]}'
                    f'__{payload["target_node"]}.{payload["target_port"]}'
                )
                if payload.get('id') != next_edge_id:
                    payload['id'] = next_edge_id
                    changed = True
            return changed
        if isinstance(payload, list):
            changed = False
            for item in payload:
                if cls._rename_node_refs_in_json(item, old_node_id, new_node_id):
                    changed = True
            return changed
        return False

    @staticmethod
    def _dict_represents_node_payload(payload: dict[str, Any]) -> bool:
        return 'kind' in payload and 'title' in payload

    @staticmethod
    def _dict_represents_edge_payload(payload: dict[str, Any]) -> bool:
        required = {'source_node', 'source_port', 'target_node', 'target_port'}
        return required.issubset(payload)

    @staticmethod
    def _run_target_references_node(target: dict[str, Any] | None, node_id: str) -> bool:
        if target is None:
            return False
        if str(target.get('node_id') or '') == node_id:
            return True
        for key in ('node_ids', 'plan'):
            values = target.get(key)
            if isinstance(values, list) and any(str(value) == node_id for value in values):
                return True
        return False

    @staticmethod
    def _run_failure_references_node(failure: dict[str, Any] | None, node_id: str) -> bool:
        if failure is None:
            return False
        return str(failure.get('node_id') or '') == node_id

    @staticmethod
    def _row_to_asset_declaration(row: sqlite3.Row) -> dict[str, Any]:
        return {
            'node_id': row['node_id'],
            'name': row['asset_name'],
            'title': row['title'],
            'description': row['description'],
            'declared_asset_type': row['declared_asset_type'],
            'declaration_index': row['declaration_index'],
            'source_hash': row['source_hash'],
            'updated_at': row['updated_at'],
        }

    @staticmethod
    def _asset_objects_for_version_ids(
        connection: sqlite3.Connection,
        version_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        if not version_ids:
            return {}
        version_id_set = set(version_ids)
        rows = connection.execute(
            'SELECT asset_version_id, object_role, object_index, artifact_hash, metadata_json '
            'FROM asset_version_objects '
            'ORDER BY asset_version_id, object_role, object_index',
        ).fetchall()
        objects_by_version: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            asset_version_id = int(row['asset_version_id'])
            if asset_version_id not in version_id_set:
                continue
            objects_by_version.setdefault(asset_version_id, []).append(
                {
                    'object_role': row['object_role'],
                    'object_index': row['object_index'],
                    'artifact_hash': row['artifact_hash'],
                    'metadata': None if row['metadata_json'] is None else json.loads(str(row['metadata_json'])),
                }
            )
        return objects_by_version

    @staticmethod
    def _row_to_asset(row: sqlite3.Row, objects: list[dict[str, Any]]) -> dict[str, Any]:
        source_hash = row['source_hash'] if row['source_hash'] is not None else row['declaration_source_hash']
        definition = None if row['definition_json'] is None else json.loads(str(row['definition_json']))
        title = row['title']
        description = row['description']
        if isinstance(definition, dict):
            versioned_title = definition.get('display_title')
            if isinstance(versioned_title, str) and versioned_title.strip():
                title = versioned_title
            versioned_description = definition.get('description')
            if versioned_description is None or isinstance(versioned_description, str):
                description = versioned_description
        return {
            'node_id': row['node_id'],
            'asset_name': row['asset_name'],
            'title': title,
            'description': description,
            'declared_asset_type': row['declared_asset_type'],
            'declaration_index': row['declaration_index'],
            'current_asset_version_id': row['current_asset_version_id'],
            'state': row['state'],
            'asset_type': row['asset_type'],
            'interactive': None if row['interactive'] is None else bool(row['interactive']),
            'source_hash': source_hash,
            'upstream_code_hash': row['upstream_code_hash'],
            'upstream_data_hash': row['upstream_data_hash'],
            'run_id': row['run_id'],
            'lineage_mode': row['lineage_mode'],
            'definition': definition,
            'modifier_schema': []
            if row['modifier_schema_json'] is None
            else json.loads(str(row['modifier_schema_json'])),
            'default_modifiers': {}
            if row['default_modifiers_json'] is None
            else json.loads(str(row['default_modifiers_json'])),
            'override_schema_hash': row['override_schema_hash'],
            'warnings': [] if row['warning_json'] is None else json.loads(str(row['warning_json'])),
            'created_at': row['created_at'],
            'objects': objects,
        }

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
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    try:
        size_bytes = path.stat().st_size
        with path.open('rb') as handle:
            if size_bytes > LOG_PREVIEW_TAIL_READ_BYTES:
                handle.seek(-LOG_PREVIEW_TAIL_READ_BYTES, os.SEEK_END)
            payload = handle.read()
    except OSError:
        return None
    preview = payload.decode('utf-8', errors='replace')
    truncated = size_bytes > len(payload)
    if len(preview) > LOG_PREVIEW_MAX_CHARS:
        preview = preview[-LOG_PREVIEW_MAX_CHARS:]
        truncated = True
    return {'text': preview, 'truncated': truncated, 'size_bytes': size_bytes}
