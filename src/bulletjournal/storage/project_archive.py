from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import nullcontext, suppress
from enum import StrEnum
from pathlib import Path, PurePosixPath

from bulletjournal.config import GRAPH_SCHEMA_VERSION, PROJECT_SCHEMA_VERSION
from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.storage.project_fs import (
    ProjectPaths,
    load_project_json,
    require_project_root,
    validate_project_id,
    validate_project_schema_version,
)
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.storage.state_db import StateDB
from bulletjournal.utils import utc_now_iso

EXCLUDED_NAMES = {'.DS_Store'}
EXCLUDED_DIR_NAMES = {'__marimo__', '__pycache__', '.runtime', '.venv', 'venv'}
REQUIRED_EXPORT_FILES = {
    'graph/meta.json',
    'graph/nodes.json',
    'graph/edges.json',
    'graph/layout.json',
    'metadata/project.json',
    'metadata/state.db',
    'pyproject.toml',
    'uv.lock',
}
REQUIRED_EXPORT_DIRECTORIES = {
    'graph',
    'notebooks',
    'objects',
    'metadata',
    'checkpoints',
}
CODE_AND_CONSTANTS_MAX_BYTES = 1_000_000


class ProjectExportMode(StrEnum):
    CODE_ONLY = 'code_only'
    CODE_AND_CONSTANTS = 'code_and_constants'
    CODE_AND_DATA = 'code_and_data'
    SNAPSHOT = 'snapshot'
    FULL = 'full'


def export_project_archive(
    project_root: Path,
    archive_path: Path,
    *,
    mode: ProjectExportMode = ProjectExportMode.FULL,
) -> dict[str, object]:
    archive = archive_path.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    resolved_mode = ProjectExportMode(mode)
    paths, project_json, use_full_export_fallback = _resolve_project_for_export(project_root, mode=resolved_mode)
    if use_full_export_fallback:
        _write_archive_from_root(paths.root, archive, include_required_directories=False)
        return {
            'archive_path': str(archive),
            'project_id': str(project_json['project_id']),
            'mode': resolved_mode.value,
        }
    with tempfile.TemporaryDirectory(prefix='bulletjournal-export-') as temp_dir:
        staged_root = Path(temp_dir) / 'project'
        barrier = (
            ProjectLock(paths.project_lock_path).exclusive()
            if resolved_mode == ProjectExportMode.SNAPSHOT
            else nullcontext()
        )
        with barrier:
            _stage_project_for_export(paths, staged_root, mode=resolved_mode)
            if resolved_mode == ProjectExportMode.SNAPSHOT:
                _stage_snapshot_state(paths, ProjectPaths(staged_root))
            else:
                _reconcile_staged_state_db(ProjectPaths(staged_root), mode=resolved_mode)
        require_project_root(staged_root)
        if resolved_mode == ProjectExportMode.SNAPSHOT:
            shutil.rmtree(ProjectPaths(staged_root).temp_dir)
        _write_archive_from_root(staged_root, archive)
    return {
        'archive_path': str(archive),
        'project_id': str(project_json['project_id']),
        'mode': resolved_mode.value,
    }


def _resolve_project_for_export(
    project_root: Path,
    *,
    mode: ProjectExportMode,
) -> tuple[ProjectPaths, dict[str, object], bool]:
    resolved_root = project_root.resolve()
    paths = ProjectPaths(resolved_root)
    if mode == ProjectExportMode.FULL:
        project_json = _load_project_json_for_full_export(paths)
        if project_json is not None:
            validate_project_id(str(project_json.get('project_id') or ''))
            if not _project_schema_supports_staged_export(project_json, source=str(paths.project_json_path)):
                return paths, project_json, True
    paths = require_project_root(resolved_root)
    return paths, load_project_json(paths), False


def _load_project_json_for_full_export(paths: ProjectPaths) -> dict[str, object] | None:
    try:
        payload = json.loads(paths.project_json_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ProjectValidationError(f'{paths.project_json_path} is not valid JSON.') from exc
    if not isinstance(payload, dict):
        raise ProjectValidationError(f'{paths.project_json_path} must be a JSON object.')
    return payload


def _project_schema_supports_staged_export(project_json: dict[str, object], *, source: str) -> bool:
    raw_version = project_json.get('schema_version')
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ProjectValidationError(f'{source} is missing a valid `schema_version`.') from exc
    if version != PROJECT_SCHEMA_VERSION:
        return False
    try:
        validate_project_schema_version(project_json, source=source)
    except ProjectValidationError:
        raise
    return True


def import_project_archive(archive_path: Path, destination_root: Path) -> dict[str, object]:
    archive = archive_path.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f'Archive not found: {archive}')
    destination = destination_root.resolve()
    if destination.exists():
        raise ProjectValidationError(f'Import destination already exists: {destination}')
    temp_root = destination.parent / f'.{destination.name}.import.tmp'
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            _validate_archive_members(names)
            zf.extractall(temp_root)
        _restore_required_directories(temp_root)
        _validate_imported_project_metadata(ProjectPaths(temp_root))
        StateDB(ProjectPaths(temp_root).state_db_path)
        _rewrite_imported_state_paths(ProjectPaths(temp_root))
        extracted_paths = require_project_root(temp_root)
        project_json = load_project_json(extracted_paths)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_root.rename(destination)
        return {'project_root': str(destination), 'project_id': str(project_json['project_id'])}
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def _stage_project_for_export(paths: ProjectPaths, staged_root: Path, *, mode: ProjectExportMode) -> None:
    staged_paths = ProjectPaths(staged_root)
    for directory in [
        staged_paths.graph_dir,
        staged_paths.notebooks_dir,
        staged_paths.dashboards_dir,
        staged_paths.metadata_dir,
        staged_paths.object_store_dir,
        staged_paths.checkpoints_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    _copy_directory(paths.graph_dir, staged_paths.graph_dir)
    _copy_directory(paths.notebooks_dir, staged_paths.notebooks_dir)
    _copy_directory(paths.dashboards_dir, staged_paths.dashboards_dir)
    if mode == ProjectExportMode.SNAPSHOT:
        shutil.copy2(paths.project_json_path, staged_paths.project_json_path)
    else:
        _copy_directory(paths.metadata_dir, staged_paths.metadata_dir)
    _copy_sqlite_database(paths.state_db_path, staged_paths.state_db_path)
    shutil.copy2(paths.pyproject_path, staged_paths.pyproject_path)
    shutil.copy2(paths.uv_lock_path, staged_paths.uv_lock_path)

    if mode == ProjectExportMode.CODE_AND_CONSTANTS:
        _copy_exportable_constant_objects(paths, staged_paths)
    if mode in {ProjectExportMode.CODE_AND_DATA, ProjectExportMode.FULL}:
        _copy_directory(paths.object_store_dir, staged_paths.object_store_dir)
        _copy_directory(paths.uploads_dir, staged_paths.uploads_dir)
    if mode == ProjectExportMode.FULL:
        _copy_directory(paths.checkpoints_dir, staged_paths.checkpoints_dir)


def _stage_snapshot_state(source_paths: ProjectPaths, paths: ProjectPaths) -> None:
    """Build a fresh database so deleted history cannot remain in SQLite free pages."""
    StateDB(paths.state_db_path)
    captured_db = paths.metadata_dir / 'captured.db'
    paths.state_db_path.rename(captured_db)
    StateDB(paths.state_db_path)
    nodes = json.loads((paths.graph_dir / 'nodes.json').read_text(encoding='utf-8'))
    with sqlite3.connect(captured_db) as source, sqlite3.connect(paths.state_db_path) as target:
        source.execute('CREATE TEMP TABLE snapshot_nodes (node_id TEXT PRIMARY KEY, incarnation_id TEXT)')
        source.executemany(
            'INSERT INTO snapshot_nodes VALUES (?, ?)',
            [(node['id'], node.get('incarnation_id')) for node in nodes],
        )
        live = (
            'node_id IN (SELECT node_id FROM snapshot_nodes) AND '
            '(incarnation_id IS NULL OR incarnation_id IN (SELECT incarnation_id FROM snapshot_nodes))'
        )
        source.execute(
            f'CREATE TEMP VIEW snapshot_artifact_versions AS SELECT av.* FROM artifact_versions av '  # noqa: S608 - internal predicates
            f'JOIN artifact_heads ah ON ah.current_version_id = av.version_id '
            f'WHERE ah.rowid IN (SELECT rowid FROM artifact_heads WHERE {live})'
        )
        source.execute(
            f'CREATE TEMP VIEW snapshot_asset_versions AS SELECT av.* FROM asset_versions av '  # noqa: S608
            f'JOIN asset_heads ah ON ah.current_asset_version_id = av.asset_version_id '
            f'WHERE ah.rowid IN (SELECT rowid FROM asset_heads WHERE {live})'
        )
        source.execute(
            'CREATE TEMP VIEW snapshot_objects AS SELECT artifact_hash FROM snapshot_artifact_versions UNION '
            'SELECT artifact_hash FROM asset_version_objects WHERE asset_version_id IN '
            '(SELECT asset_version_id FROM snapshot_asset_versions)'
        )
        tables = {
            'node_incarnations': "status = 'live' AND incarnation_id IN (SELECT incarnation_id FROM snapshot_nodes)",
            'objects': 'artifact_hash IN (SELECT artifact_hash FROM snapshot_objects)',
            'artifact_versions': 'version_id IN (SELECT version_id FROM snapshot_artifact_versions)',
            'asset_versions': 'asset_version_id IN (SELECT asset_version_id FROM snapshot_asset_versions)',
            'artifact_heads': live,
            'asset_heads': live,
            'asset_version_objects': 'asset_version_id IN (SELECT asset_version_id FROM snapshot_asset_versions)',
            'asset_declarations': live,
            'asset_annotations': 'node_id IN (SELECT node_id FROM snapshot_nodes)',
            'notebook_execution_heads': live,
            'orchestrator_execution_meta': live,
            'notebook_revisions': (
                # Keep the latest parsed contract even when current source has syntax errors.
                f'{live} AND rowid IN (SELECT MAX(rowid) FROM notebook_revisions '  # noqa: S608
                'GROUP BY node_id, incarnation_id)'
            ),
            'validation_issues': 'node_id IN (SELECT node_id FROM snapshot_nodes)',
            'validation_issue_dismissals': (
                'issue_id IN (SELECT issue_id FROM validation_issues '
                'WHERE node_id IN (SELECT node_id FROM snapshot_nodes))'
            ),
            'persistent_notices': f'node_id IS NULL OR ({live})',
            # These are the latest summaries, not the full run history.
            'run_records': (
                f'run_id IN (SELECT run_id FROM orchestrator_execution_meta WHERE {live} '  # noqa: S608
                f'UNION SELECT run_id FROM notebook_execution_heads WHERE {live})'
            ),
            'publication_batches': (
                'publication_id IN (SELECT publication_id FROM snapshot_artifact_versions '
                'UNION SELECT publication_id FROM snapshot_asset_versions)'
            ),
            'run_inputs': (
                'publication_id IN (SELECT publication_id FROM snapshot_artifact_versions '
                'UNION SELECT publication_id FROM snapshot_asset_versions)'
            ),
            # Restore this last: annotation triggers update favorites metadata.
            'project_meta': "key NOT IN ('gc_last_report', 'gc_last_error', 'gc_last_completed_at', 'gc_requested_at')",
        }
        target.execute('PRAGMA foreign_keys = ON')
        for table, predicate in tables.items():
            rows = source.execute(f'SELECT * FROM {table} WHERE {predicate}')  # noqa: S608 - internal table/predicate allowlist
            columns = ', '.join(f'"{column[0]}"' for column in rows.description)
            placeholders = ', '.join('?' for _ in rows.description)
            target.executemany(
                f'INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})',  # noqa: S608
                rows.fetchall(),
            )
        target.execute('UPDATE orchestrator_execution_meta SET stdout_path = NULL, stderr_path = NULL')
        target.execute(
            "UPDATE objects SET gc_state = 'active', gc_marked_at = NULL, quarantined_at = NULL, "
            'delete_after = NULL, quarantine_path = NULL, unreferenced_at = NULL'
        )
        # Provenance may reference an older override source; do not leave dangling version pointers.
        target.execute(
            'UPDATE artifact_versions SET override_source_version_id = NULL '
            'WHERE override_source_version_id NOT IN (SELECT version_id FROM artifact_versions)'
        )
        target.execute(
            'UPDATE asset_versions SET override_source_version_id = NULL '
            'WHERE override_source_version_id NOT IN (SELECT asset_version_id FROM asset_versions)'
        )
        expected_hashes = {row[0] for row in source.execute('SELECT artifact_hash FROM snapshot_objects')}
        objects = target.execute('SELECT artifact_hash, size_bytes FROM objects').fetchall()
        if {row[0] for row in objects} != expected_hashes:
            raise ProjectValidationError('Snapshot references objects missing from the object registry.')
        for artifact_hash, size_bytes in objects:
            relative = Path(artifact_hash[:2]) / artifact_hash[2:]
            source_object = source_paths.object_store_dir / relative
            if not source_object.is_file():
                source_object = source_paths.object_quarantine_dir / relative
            destination = paths.object_store_dir / relative
            if not source_object.is_file():
                raise ProjectValidationError(f'Snapshot object is missing: {artifact_hash}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_object, destination)
            with destination.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            if destination.stat().st_size != size_bytes or digest != artifact_hash:
                raise ProjectValidationError(f'Snapshot object is corrupt: {artifact_hash}')
        if target.execute('PRAGMA foreign_key_check').fetchall():
            raise ProjectValidationError('Snapshot state contains invalid foreign-key references.')
        target.commit()
        _checkpoint_sqlite_database(target)
    captured_db.unlink()


def _copy_directory(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    for child in sorted(source.iterdir()):
        if _should_exclude_path(child):
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_directory(child, target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)


def _exportable_constant_versions(paths: ProjectPaths) -> list[tuple[int, str]]:
    nodes = json.loads((paths.graph_dir / 'nodes.json').read_text(encoding='utf-8'))
    constant_ids = {
        str(node['id'])
        for node in nodes
        if isinstance(node, dict) and node.get('kind') == NodeKind.CONSTANT.value and isinstance(node.get('id'), str)
    }
    with sqlite3.connect(paths.state_db_path) as connection:
        rows = connection.execute(
            'SELECT ah.node_id, ah.current_version_id, av.artifact_hash FROM artifact_heads ah '
            'JOIN artifact_versions av ON av.version_id = ah.current_version_id '
            'JOIN objects ao ON ao.artifact_hash = av.artifact_hash '
            'WHERE ao.size_bytes <= ? '
            'ORDER BY ah.current_version_id',
            (CODE_AND_CONSTANTS_MAX_BYTES,),
        ).fetchall()
    return [
        (int(version_id), str(artifact_hash))
        for node_id, version_id, artifact_hash in rows
        if str(node_id) in constant_ids
    ]


def _copy_exportable_constant_objects(source_paths: ProjectPaths, staged_paths: ProjectPaths) -> None:
    for _, artifact_hash in _exportable_constant_versions(source_paths):
        source = source_paths.object_store_dir / artifact_hash[:2] / artifact_hash[2:]
        if not source.is_file():
            continue
        destination = staged_paths.object_store_dir / artifact_hash[:2] / artifact_hash[2:]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)


def _should_exclude_path(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return True
    if path.name in EXCLUDED_DIR_NAMES:
        return True
    return path.name.endswith('.db-shm') or path.name.endswith('.db-wal')


def _reconcile_staged_state_db(paths: ProjectPaths, *, mode: ProjectExportMode) -> None:
    StateDB(paths.state_db_path)
    now = utc_now_iso()
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('UPDATE orchestrator_execution_meta SET stdout_path = NULL, stderr_path = NULL')
        if mode in {ProjectExportMode.CODE_ONLY, ProjectExportMode.CODE_AND_CONSTANTS}:
            preserved_versions = (
                _exportable_constant_versions(paths) if mode == ProjectExportMode.CODE_AND_CONSTANTS else []
            )
            connection.execute('CREATE TEMP TABLE export_constant_versions (version_id INTEGER PRIMARY KEY)')
            connection.executemany(
                'INSERT INTO export_constant_versions (version_id) VALUES (?)',
                [(version_id,) for version_id, _ in preserved_versions],
            )
            connection.execute(
                'UPDATE artifact_heads SET current_version_id = NULL, state = ? '
                'WHERE current_version_id NOT IN (SELECT version_id FROM export_constant_versions)',
                (ArtifactState.PENDING.value,),
            )
            connection.execute(
                'UPDATE asset_heads SET current_asset_version_id = NULL, state = ?',
                (ArtifactState.PENDING.value,),
            )
            connection.execute(
                'UPDATE notebook_execution_heads SET '
                'state = ?, source_hash = NULL, upstream_code_hash = NULL, upstream_data_hash = NULL, '
                'run_id = NULL, last_run_started_at = NULL, last_run_finished_at = NULL, updated_at = ?',
                (ArtifactState.PENDING.value, now),
            )
            connection.execute('DELETE FROM run_inputs')
            connection.execute('DELETE FROM run_outputs')
            connection.execute('DELETE FROM orchestrator_execution_meta')
            connection.execute('DELETE FROM run_records')
            connection.execute('DELETE FROM asset_version_objects')
            connection.execute(
                'DELETE FROM artifact_versions '
                'WHERE version_id NOT IN (SELECT version_id FROM export_constant_versions)'
            )
            connection.execute('DELETE FROM asset_versions')
            connection.execute('DELETE FROM object_pins')
            connection.execute('DELETE FROM object_leases')
            connection.execute(
                'DELETE FROM objects WHERE artifact_hash NOT IN (SELECT artifact_hash FROM artifact_versions)'
            )
            connection.execute('DELETE FROM persistent_notices WHERE code IN (?, ?)', ('run_failed', 'run_warning'))
        if mode in {
            ProjectExportMode.CODE_ONLY,
            ProjectExportMode.CODE_AND_CONSTANTS,
            ProjectExportMode.CODE_AND_DATA,
        }:
            connection.execute('DELETE FROM checkpoints')
        else:
            rows = connection.execute('SELECT checkpoint_id FROM checkpoints ORDER BY checkpoint_id').fetchall()
            for row in rows:
                checkpoint_id = str(row[0])
                checkpoint_path = paths.checkpoints_dir / checkpoint_id
                if checkpoint_path.exists():
                    connection.execute(
                        'UPDATE checkpoints SET path = ? WHERE checkpoint_id = ?',
                        (str(checkpoint_path), checkpoint_id),
                    )
                    continue
                connection.execute('DELETE FROM checkpoints WHERE checkpoint_id = ?', (checkpoint_id,))
        connection.commit()
        _checkpoint_sqlite_database(connection)


def _write_archive_from_root(root: Path, archive_path: Path, *, include_required_directories: bool = True) -> None:
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for directory in sorted(
            _iter_directory_members(root, include_required_directories=include_required_directories)
        ):
            zf.writestr(f'{directory}/', b'')
        for file_path in sorted(path for path in root.rglob('*') if path.is_file() and not _should_exclude_path(path)):
            zf.write(file_path, arcname=file_path.relative_to(root).as_posix())


def _iter_directory_members(root: Path, *, include_required_directories: bool) -> set[str]:
    members = set(REQUIRED_EXPORT_DIRECTORIES) if include_required_directories else set()
    for path in root.rglob('*'):
        if path.is_dir() and not _should_exclude_path(path):
            members.add(path.relative_to(root).as_posix())
    return members


def _validate_archive_members(names: set[str]) -> None:
    normalized = {_normalize_archive_name(name) for name in names if name}
    for name in normalized:
        member_path = PurePosixPath(name)
        if member_path.is_absolute() or '..' in member_path.parts:
            raise ProjectValidationError(f'Archive contains an invalid member path: {name}')
        if member_path.parts and member_path.parts[0] == 'project':
            raise ProjectValidationError('Archive must be rooted at zip root, not nested under `project/`.')
        if name == 'export_manifest.json':
            raise ProjectValidationError('Archive must not contain `export_manifest.json`.')
    missing_files = sorted(REQUIRED_EXPORT_FILES - normalized)
    if missing_files:
        raise ProjectValidationError(f'Archive is missing required members: {", ".join(missing_files)}')


def _validate_imported_project_metadata(paths: ProjectPaths) -> None:
    project_json = _load_required_json_dict(paths.project_json_path, source='Archive project metadata')
    validate_project_schema_version(project_json, source='Archive project metadata')
    project_id = validate_project_id(str(project_json.get('project_id') or ''))
    graph_meta = _load_required_json_dict(paths.graph_dir / 'meta.json', source='Archive graph metadata')
    raw_version = graph_meta.get('schema_version')
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ProjectValidationError('Archive graph metadata is missing a valid `schema_version`.') from exc
    if version != GRAPH_SCHEMA_VERSION:
        raise ProjectValidationError(
            f'Unsupported BulletJournal graph schema version {version}; expected {GRAPH_SCHEMA_VERSION}.'
        )
    graph_project_id = validate_project_id(str(graph_meta.get('project_id') or ''))
    if graph_project_id != project_id:
        raise ProjectValidationError(
            f'Archive graph metadata has project_id {graph_project_id!r}, expected {project_id!r}.'
        )


def _load_required_json_dict(path: Path, *, source: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ProjectValidationError(f'{source} is missing.') from exc
    except json.JSONDecodeError as exc:
        raise ProjectValidationError(f'{source} is not valid JSON.') from exc
    if not isinstance(payload, dict):
        raise ProjectValidationError(f'{source} must be a JSON object.')
    return payload


def _rewrite_imported_state_paths(paths: ProjectPaths) -> None:
    with sqlite3.connect(paths.state_db_path) as connection:
        rows = connection.execute('SELECT checkpoint_id FROM checkpoints ORDER BY checkpoint_id').fetchall()
        for row in rows:
            checkpoint_id = str(row[0])
            checkpoint_path = paths.checkpoints_dir / checkpoint_id
            if checkpoint_path.exists():
                connection.execute(
                    'UPDATE checkpoints SET path = ? WHERE checkpoint_id = ?',
                    (str(checkpoint_path), checkpoint_id),
                )
                continue
            connection.execute('DELETE FROM checkpoints WHERE checkpoint_id = ?', (checkpoint_id,))
        connection.execute('UPDATE orchestrator_execution_meta SET stdout_path = NULL, stderr_path = NULL')
        connection.commit()
        _checkpoint_sqlite_database(connection)


def _checkpoint_sqlite_database(connection: sqlite3.Connection) -> None:
    with suppress(sqlite3.OperationalError):
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def _normalize_archive_name(name: str) -> str:
    return name[:-1] if name.endswith('/') else name


def _restore_required_directories(root: Path) -> None:
    paths = ProjectPaths(root)
    for directory in [
        paths.graph_dir,
        paths.notebooks_dir,
        paths.dashboards_dir,
        paths.metadata_dir,
        paths.object_store_dir,
        paths.checkpoints_dir,
        paths.uploads_dir,
        paths.pulled_files_dir,
        paths.worker_temp_dir,
        paths.execution_logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
