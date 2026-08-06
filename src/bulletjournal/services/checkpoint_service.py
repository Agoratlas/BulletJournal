from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import NotFoundError
from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.hashing import combine_hashes, hash_json, sha256_bytes
from bulletjournal.domain.models import file_input_artifact_name
from bulletjournal.execution.planner import topological_nodes
from bulletjournal.storage.atomic_write import atomic_write_text
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.utils import copy_tree, json_dumps, utc_now_iso

AUTO_CHECKPOINT_INTERVAL = timedelta(minutes=10)
HEADS_SCHEMA_VERSION = 1


class CheckpointService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def create_checkpoint(self) -> dict[str, object]:
        project = self.project_service.require_project()
        checkpoint_id = self._next_checkpoint_id()
        checkpoint_dir = project.paths.checkpoints_dir / checkpoint_id
        staging_dir = project.paths.checkpoints_dir / f'.{checkpoint_id}.{uuid.uuid4().hex}.tmp'
        lock = ProjectLock(project.paths.project_lock_path)
        try:
            with lock.exclusive():
                graph = self.project_service.graph()
                graph_version = int(graph.meta['graph_version'])
                manifest = self._capture_heads(checkpoint_id, graph_version, graph.nodes)
                copy_tree(project.paths.graph_dir, staging_dir / 'graph')
                copy_tree(project.paths.notebooks_dir, staging_dir / 'notebooks')
                copy_tree(project.paths.dashboards_dir, staging_dir / 'dashboards')
                atomic_write_text(staging_dir / 'heads.json', json_dumps(manifest, pretty=True) + '\n')
                os.replace(staging_dir, checkpoint_dir)
                project.state_db.create_checkpoint(checkpoint_id, graph_version, str(checkpoint_dir))
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
        self.project_service.event_service.publish(
            'checkpoint.created',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={'checkpoint_id': checkpoint_id, 'path': str(checkpoint_dir)},
        )
        return {'checkpoint_id': checkpoint_id, 'path': str(checkpoint_dir), 'graph_version': graph_version}

    def create_automatic_checkpoint_if_due(self) -> dict[str, object] | None:
        project = self.project_service.require_project()
        latest = next(iter(project.state_db.list_checkpoints()), None)
        if latest is not None and not self._checkpoint_is_due(latest.created_at):
            return None
        return self.create_checkpoint()

    def list_checkpoints(self) -> list[dict[str, object]]:
        return [asdict(checkpoint) for checkpoint in self.project_service.require_project().state_db.list_checkpoints()]

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, object]:
        project = self.project_service.require_project()
        checkpoints = {checkpoint.checkpoint_id: checkpoint for checkpoint in project.state_db.list_checkpoints()}
        checkpoint = checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise NotFoundError(f'Unknown checkpoint `{checkpoint_id}`.')
        checkpoint_path = Path(checkpoint.path)
        if not checkpoint_path.exists():
            raise FileNotFoundError(f'Checkpoint path missing: {checkpoint_path}')
        manifest, manifest_error = self._read_manifest(checkpoint_path / 'heads.json', checkpoint_id)
        with (
            ProjectLock(project.paths.project_lock_path).exclusive(),
            self.project_service.suspend_automatic_checkpoints(),
        ):
            if project.paths.graph_dir.exists():
                shutil.rmtree(project.paths.graph_dir)
            if project.paths.notebooks_dir.exists():
                shutil.rmtree(project.paths.notebooks_dir)
            if project.paths.dashboards_dir.exists():
                shutil.rmtree(project.paths.dashboards_dir)
            copy_tree(checkpoint_path / 'graph', project.paths.graph_dir)
            copy_tree(checkpoint_path / 'notebooks', project.paths.notebooks_dir)
            if (checkpoint_path / 'dashboards').exists():
                copy_tree(checkpoint_path / 'dashboards', project.paths.dashboards_dir)
            else:
                project.paths.dashboards_dir.mkdir(parents=True, exist_ok=True)
            graph = self.project_service.graph()
            self._resolve_incarnations(graph, manifest)
            project.graph_store.write(graph, increment_version=False)
            self._drop_state_for_missing_nodes()
            self._clear_restored_heads(graph)
            self.project_service.reparse_all_notebooks()
            self._reconcile_artifact_state()
            if manifest is None:
                self._mark_restored_notebooks_stale()
            else:
                self._restore_heads(checkpoint_id, graph, manifest)
            if manifest_error is not None:
                self._notice(
                    checkpoint_id,
                    None,
                    'checkpoint_heads_unavailable',
                    'Checkpoint head metadata is unavailable; graph and source were restored without saved heads.',
                    {'reason': manifest_error},
                )
        project.state_db.mark_checkpoint_restored(checkpoint_id)
        self.project_service.event_service.publish(
            'checkpoint.restored',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={'checkpoint_id': checkpoint_id},
        )
        return {'checkpoint_id': checkpoint_id, 'status': 'restored'}

    def _capture_heads(self, checkpoint_id: str, graph_version: int, nodes: list[Any]) -> dict[str, Any]:
        project = self.project_service.require_project()
        captured_nodes: list[dict[str, Any]] = []
        with project.state_db._connection() as connection:
            connection.execute('BEGIN')
            for node in nodes:
                incarnation_id = node.incarnation_id
                artifact_rows = connection.execute(
                    'SELECT ah.*, av.*, o.storage_kind, o.data_type, o.size_bytes, o.extension, o.mime_type, '
                    'o.preview_json, o.gc_state FROM artifact_heads ah LEFT JOIN artifact_versions av '
                    'ON av.version_id = ah.current_version_id LEFT JOIN objects o '
                    'ON o.artifact_hash = av.artifact_hash '
                    'WHERE ah.incarnation_id = ? ORDER BY ah.artifact_name',
                    (incarnation_id,),
                ).fetchall()
                asset_rows = connection.execute(
                    'SELECT ah.*, av.* FROM asset_heads ah LEFT JOIN asset_versions av '
                    'ON av.asset_version_id = ah.current_asset_version_id WHERE ah.incarnation_id = ? '
                    'ORDER BY ah.asset_name',
                    (incarnation_id,),
                ).fetchall()
                execution = connection.execute(
                    'SELECT * FROM notebook_execution_heads WHERE incarnation_id = ?', (incarnation_id,)
                ).fetchone()
                artifacts = [self._artifact_manifest_entry(dict(row)) for row in artifact_rows]
                assets: list[dict[str, Any]] = []
                for row in asset_rows:
                    entry = self._asset_manifest_entry(dict(row))
                    version_id = row['current_asset_version_id']
                    if version_id is not None:
                        object_rows = connection.execute(
                            'SELECT avo.object_role, avo.object_index, avo.artifact_hash, avo.metadata_json, '
                            'o.storage_kind, o.data_type, o.size_bytes, o.extension, o.mime_type, o.preview_json, '
                            'o.gc_state FROM asset_version_objects avo LEFT JOIN objects o '
                            'ON o.artifact_hash = avo.artifact_hash WHERE avo.asset_version_id = ? '
                            'ORDER BY avo.object_role, avo.object_index',
                            (version_id,),
                        ).fetchall()
                        entry['objects'] = [self._object_manifest_entry(dict(item)) for item in object_rows]
                    assets.append(entry)
                captured_nodes.append(
                    {
                        'incarnation_id': incarnation_id,
                        'node_id': node.id,
                        'node_kind': node.kind.value,
                        'artifact_heads': artifacts,
                        'asset_heads': assets,
                        'execution_head': None
                        if execution is None
                        else self._execution_manifest_entry(dict(execution)),
                    }
                )
        payload: dict[str, Any] = {
            'schema_version': HEADS_SCHEMA_VERSION,
            'checkpoint_id': checkpoint_id,
            'graph_version': graph_version,
            'created_at': utc_now_iso(),
            'nodes': captured_nodes,
        }
        payload['checksum'] = hash_json(payload)
        return payload

    @staticmethod
    def _artifact_manifest_entry(row: dict[str, Any]) -> dict[str, Any]:
        entry = {
            'artifact_name': row['artifact_name'],
            'state': row['state'],
            'version_id': row.get('current_version_id'),
        }
        if row.get('current_version_id') is not None:
            entry['version'] = {
                key: row.get(key)
                for key in (
                    'role',
                    'artifact_hash',
                    'source_hash',
                    'upstream_code_hash',
                    'upstream_data_hash',
                    'run_id',
                    'lineage_mode',
                    'created_at',
                )
            }
            entry['version']['warnings'] = json.loads(row.get('warning_json') or '[]')
            entry['object'] = CheckpointService._object_manifest_entry(row)
        return entry

    @staticmethod
    def _asset_manifest_entry(row: dict[str, Any]) -> dict[str, Any]:
        entry = {
            'asset_name': row['asset_name'],
            'state': row['state'],
            'asset_version_id': row.get('current_asset_version_id'),
            'objects': [],
        }
        if row.get('current_asset_version_id') is not None:
            entry['version'] = {
                key: row.get(key)
                for key in (
                    'asset_type',
                    'interactive',
                    'source_hash',
                    'upstream_code_hash',
                    'upstream_data_hash',
                    'run_id',
                    'lineage_mode',
                    'override_schema_hash',
                    'created_at',
                )
            }
            for source, target, fallback in (
                ('definition_json', 'definition', '{}'),
                ('modifier_schema_json', 'modifier_schema', '[]'),
                ('default_modifiers_json', 'default_modifiers', '{}'),
                ('warning_json', 'warnings', '[]'),
            ):
                entry['version'][target] = json.loads(row.get(source) or fallback)
        return entry

    @staticmethod
    def _object_manifest_entry(row: dict[str, Any]) -> dict[str, Any]:
        entry = {
            key: row.get(key)
            for key in (
                'object_role',
                'object_index',
                'artifact_hash',
                'storage_kind',
                'data_type',
                'size_bytes',
                'extension',
                'mime_type',
                'gc_state',
            )
            if key in row
        }
        entry['metadata'] = json.loads(row['metadata_json']) if row.get('metadata_json') else None
        entry['preview'] = json.loads(row['preview_json']) if row.get('preview_json') else None
        return entry

    @staticmethod
    def _execution_manifest_entry(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                'state',
                'source_hash',
                'upstream_code_hash',
                'upstream_data_hash',
                'run_id',
                'last_run_started_at',
                'last_run_finished_at',
                'updated_at',
            )
        }

    def _read_manifest(self, path: Path, checkpoint_id: str) -> tuple[dict[str, Any] | None, str | None]:
        if not path.exists():
            return None, 'legacy_checkpoint'
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(manifest, dict) or manifest.get('schema_version') != HEADS_SCHEMA_VERSION:
                return None, 'unsupported_schema'
            checksum = manifest.get('checksum')
            unsigned = {key: value for key, value in manifest.items() if key != 'checksum'}
            if not isinstance(checksum, str) or checksum != hash_json(unsigned):
                return None, 'checksum_mismatch'
            if manifest.get('checkpoint_id') != checkpoint_id or not isinstance(manifest.get('nodes'), list):
                return None, 'identity_mismatch'
            return manifest, None
        except (OSError, ValueError, TypeError):
            return None, 'malformed_manifest'

    def _resolve_incarnations(self, graph, manifest: dict[str, Any] | None) -> None:
        project = self.project_service.require_project()
        manifest_nodes = {
            str(item.get('node_id')): item for item in (manifest or {}).get('nodes', []) if isinstance(item, dict)
        }
        now = utc_now_iso()
        with project.state_db._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            restored_ids = {node.id for node in graph.nodes}
            live_rows = connection.execute(
                "SELECT incarnation_id, node_id FROM node_incarnations WHERE status = 'live'"
            ).fetchall()
            connection.executemany(
                "UPDATE node_incarnations SET status = 'expired', expired_at = ? WHERE incarnation_id = ?",
                [(now, row['incarnation_id']) for row in live_rows if row['node_id'] not in restored_ids],
            )
            for node in graph.nodes:
                saved = manifest_nodes.get(node.id)
                checkpoint_incarnation = None if saved is None else saved.get('incarnation_id')
                if checkpoint_incarnation != node.incarnation_id:
                    checkpoint_incarnation = None
                live = connection.execute(
                    "SELECT incarnation_id FROM node_incarnations WHERE node_id = ? AND status = 'live'", (node.id,)
                ).fetchone()
                known = (
                    None
                    if checkpoint_incarnation is None
                    else connection.execute(
                        'SELECT * FROM node_incarnations WHERE incarnation_id = ?', (checkpoint_incarnation,)
                    ).fetchone()
                )
                use_saved = known is not None and (
                    str(known['status']) == 'live' or str(known['status']) == 'tombstoned'
                )
                if use_saved:
                    if live is not None and str(live['incarnation_id']) != checkpoint_incarnation:
                        connection.execute(
                            "UPDATE node_incarnations SET status = 'expired', expired_at = ? WHERE incarnation_id = ?",
                            (now, live['incarnation_id']),
                        )
                    connection.execute(
                        "UPDATE node_incarnations SET node_id = ?, node_kind = ?, status = 'live', "
                        'tombstoned_at = NULL, expired_at = NULL WHERE incarnation_id = ?',
                        (node.id, node.kind.value, checkpoint_incarnation),
                    )
                    connection.execute(
                        "UPDATE node_tombstones SET status = 'restored', restored_at = ? "
                        "WHERE incarnation_id = ? AND status = 'retained'",
                        (now, checkpoint_incarnation),
                    )
                    connection.execute(
                        "DELETE FROM object_pins WHERE owner_kind = 'tombstone' AND owner_id IN "
                        "(SELECT tombstone_id FROM node_tombstones WHERE incarnation_id = ? AND status = 'restored')",
                        (checkpoint_incarnation,),
                    )
                    node.incarnation_id = str(checkpoint_incarnation)
                    continue
                if live is not None:
                    connection.execute(
                        "UPDATE node_incarnations SET status = 'expired', expired_at = ? WHERE incarnation_id = ?",
                        (now, live['incarnation_id']),
                    )
                node.incarnation_id = str(uuid.uuid4())
                connection.execute(
                    'INSERT INTO node_incarnations '
                    '(incarnation_id, node_id, node_kind, status, generation, created_at) '
                    "VALUES (?, ?, ?, 'live', 1, ?)",
                    (node.incarnation_id, node.id, node.kind.value, now),
                )

    def _clear_restored_heads(self, graph) -> None:
        incarnation_ids = [node.incarnation_id for node in graph.nodes]
        if not incarnation_ids:
            return
        with self.project_service.require_project().state_db._connection() as connection:
            for table in ('artifact_heads', 'asset_heads', 'notebook_execution_heads'):
                connection.executemany(
                    f'DELETE FROM {table} WHERE incarnation_id = ?',  # noqa: S608 - fixed table allowlist.
                    [(incarnation_id,) for incarnation_id in incarnation_ids],
                )

    def _restore_heads(self, checkpoint_id: str, graph, manifest: dict[str, Any]) -> None:
        project = self.project_service.require_project()
        nodes_by_id = {node.id: node for node in graph.nodes}
        manifest_nodes = {str(item.get('node_id')): item for item in manifest['nodes'] if isinstance(item, dict)}
        selected_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
        selected_assets: dict[tuple[str, str], dict[str, Any]] = {}
        selected_execution: dict[str, dict[str, Any]] = {}
        for node_id in topological_nodes(graph):
            node = nodes_by_id[node_id]
            saved_node = manifest_nodes.get(node_id)
            if saved_node is None or saved_node.get('incarnation_id') != node.incarnation_id:
                continue
            for entry in saved_node.get('artifact_heads', []):
                selected_artifacts[(node_id, str(entry.get('artifact_name')))] = self._restore_artifact(
                    checkpoint_id, node, entry
                )
            for entry in saved_node.get('asset_heads', []):
                selected_assets[(node_id, str(entry.get('asset_name')))] = self._restore_asset(
                    checkpoint_id, node, entry
                )
            execution = saved_node.get('execution_head')
            if isinstance(execution, dict):
                selected_execution[node_id] = execution
        for node_id in topological_nodes(graph):
            node = nodes_by_id[node_id]
            inputs = self._checkpoint_input_lineage(node_id, graph, selected_artifacts)
            interface = self.project_service.latest_interface(node_id)
            source_hash = None if interface is None else interface.get('source_hash')
            for (owner_id, name), restored in selected_artifacts.items():
                if owner_id != node_id or restored['state'] == ArtifactState.PENDING.value:
                    continue
                version = restored['version']
                if self._lineage_matches(node, name, source_hash, inputs, version):
                    project.state_db.set_artifact_head_state(node_id, name, ArtifactState.READY)
                    restored['state'] = ArtifactState.READY.value
            for (owner_id, name), restored in selected_assets.items():
                if owner_id != node_id or restored['state'] == ArtifactState.PENDING.value:
                    continue
                if self._lineage_matches(node, name, source_hash, inputs, restored['version']):
                    project.state_db.set_asset_head_state(node_id, name, ArtifactState.READY)
                    restored['state'] = ArtifactState.READY.value
            execution = selected_execution.get(node_id)
            if execution is not None:
                self._restore_execution(node_id, execution, source_hash, inputs)

    def _restore_artifact(self, checkpoint_id: str, node, entry: Any) -> dict[str, Any]:
        name = str(entry.get('artifact_name') or '') if isinstance(entry, dict) else ''
        version = entry.get('version') if isinstance(entry, dict) else None
        object_meta = entry.get('object') if isinstance(entry, dict) else None
        if not name or entry.get('version_id') is None:
            self._set_pending_artifact(node, name)
            return {'state': ArtifactState.PENDING.value, 'version': {}}
        reason = self._validate_object(object_meta)
        if not isinstance(version, dict) or reason is not None or not self._valid_artifact_version(version):
            self._set_pending_artifact(node, name)
            self._notice_unavailable(checkpoint_id, node.id, 'artifact', name, reason or 'incomplete_metadata')
            return {'state': ArtifactState.PENDING.value, 'version': version or {}}
        db = self.project_service.require_project().state_db
        with db._connection() as connection:
            version_id = self._matching_artifact_version(connection, node.incarnation_id, entry)
            if version_id is None:
                cursor = connection.execute(
                    'INSERT INTO artifact_versions (node_id, artifact_name, role, artifact_hash, source_hash, '
                    'upstream_code_hash, upstream_data_hash, run_id, lineage_mode, created_at, warning_json, '
                    'incarnation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        node.id,
                        name,
                        version['role'],
                        version['artifact_hash'],
                        version['source_hash'],
                        version['upstream_code_hash'],
                        version['upstream_data_hash'],
                        version['run_id'],
                        version['lineage_mode'],
                        version['created_at'],
                        json_dumps(version['warnings']),
                        node.incarnation_id,
                    ),
                )
                version_id = int(cursor.lastrowid)
            connection.execute(
                'INSERT INTO artifact_heads (node_id, artifact_name, current_version_id, state, incarnation_id) '
                'VALUES (?, ?, ?, ?, ?) ON CONFLICT(node_id, artifact_name) DO UPDATE SET '
                'current_version_id = excluded.current_version_id, state = excluded.state, '
                'incarnation_id = excluded.incarnation_id',
                (node.id, name, version_id, ArtifactState.STALE.value, node.incarnation_id),
            )
        return {'state': ArtifactState.STALE.value, 'version': version}

    def _restore_asset(self, checkpoint_id: str, node, entry: Any) -> dict[str, Any]:
        name = str(entry.get('asset_name') or '') if isinstance(entry, dict) else ''
        version = entry.get('version') if isinstance(entry, dict) else None
        objects = entry.get('objects') if isinstance(entry, dict) else None
        reasons = [] if isinstance(objects, list) else ['incomplete_metadata']
        if isinstance(objects, list):
            reasons = [reason for item in objects if (reason := self._validate_object(item)) is not None]
        if (
            not name
            or entry.get('asset_version_id') is None
            or not isinstance(version, dict)
            or reasons
            or not self._valid_asset_version(version)
        ):
            self._set_pending_asset(node, name)
            self._notice_unavailable(checkpoint_id, node.id, 'asset', name, ','.join(reasons) or 'incomplete_metadata')
            return {'state': ArtifactState.PENDING.value, 'version': version or {}}
        db = self.project_service.require_project().state_db
        with db._connection() as connection:
            asset_version_id = self._matching_asset_version(connection, node.incarnation_id, entry)
            if asset_version_id is None:
                cursor = connection.execute(
                    'INSERT INTO asset_versions (node_id, asset_name, asset_type, interactive, source_hash, '
                    'upstream_code_hash, upstream_data_hash, run_id, lineage_mode, definition_json, '
                    'modifier_schema_json, default_modifiers_json, override_schema_hash, warning_json, created_at, '
                    'incarnation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        node.id,
                        name,
                        version['asset_type'],
                        int(bool(version['interactive'])),
                        version['source_hash'],
                        version['upstream_code_hash'],
                        version['upstream_data_hash'],
                        version['run_id'],
                        version['lineage_mode'],
                        json_dumps(version['definition']),
                        json_dumps(version['modifier_schema']),
                        json_dumps(version['default_modifiers']),
                        version['override_schema_hash'],
                        json_dumps(version['warnings']),
                        version['created_at'],
                        node.incarnation_id,
                    ),
                )
                asset_version_id = int(cursor.lastrowid)
                connection.executemany(
                    'INSERT INTO asset_version_objects (asset_version_id, object_role, object_index, artifact_hash, '
                    'metadata_json) VALUES (?, ?, ?, ?, ?)',
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
                'INSERT INTO asset_heads (node_id, asset_name, current_asset_version_id, state, incarnation_id) '
                'VALUES (?, ?, ?, ?, ?) ON CONFLICT(node_id, asset_name) DO UPDATE SET '
                'current_asset_version_id = excluded.current_asset_version_id, state = excluded.state, '
                'incarnation_id = excluded.incarnation_id',
                (node.id, name, asset_version_id, ArtifactState.STALE.value, node.incarnation_id),
            )
        return {'state': ArtifactState.STALE.value, 'version': version}

    def _restore_execution(self, node_id: str, entry: dict[str, Any], source_hash: Any, inputs) -> None:
        complete = bool(entry.get('run_id') and entry.get('last_run_finished_at'))
        state = ArtifactState.PENDING
        if complete:
            state = ArtifactState.STALE
            expected = self._expected_lineage(node_id, '__execution__', source_hash, inputs)
            if expected is not None and all(entry.get(key) == value for key, value in expected.items()):
                state = ArtifactState.READY
        self.project_service.require_project().state_db.upsert_notebook_execution_head(
            node_id=node_id,
            state=state,
            source_hash=entry.get('source_hash'),
            upstream_code_hash=entry.get('upstream_code_hash'),
            upstream_data_hash=entry.get('upstream_data_hash'),
            run_id=entry.get('run_id'),
            last_run_started_at=entry.get('last_run_started_at'),
            last_run_finished_at=entry.get('last_run_finished_at'),
        )

    def _checkpoint_input_lineage(self, node_id: str, graph, selected) -> list[dict[str, str]] | None:
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return None
        values: list[dict[str, str]] = []
        for port in interface.get('inputs', []):
            binding = resolve_input_binding(graph, node_id=node_id, input_name=str(port['name']))
            if binding is None:
                if not bool(port.get('has_default', False)):
                    return None
                values.append({'artifact_hash': hash_json(port.get('default')), 'upstream_code_hash': 'default'})
                continue
            restored = selected.get(binding)
            if restored is None or restored['state'] != ArtifactState.READY.value:
                return None
            version = restored['version']
            values.append(
                {'artifact_hash': version['artifact_hash'], 'upstream_code_hash': version['upstream_code_hash']}
            )
        return values

    def _lineage_matches(self, node, name: str, source_hash: Any, inputs, version: dict[str, Any]) -> bool:
        if node.kind in {NodeKind.CONSTANT, NodeKind.FILE_INPUT}:
            artifact_hash = version.get('artifact_hash')
            return (
                isinstance(artifact_hash, str)
                and version.get('source_hash') == source_hash
                and version.get('upstream_code_hash') == artifact_hash
                and version.get('upstream_data_hash') == artifact_hash
            )
        expected = self._expected_lineage(node.id, name, source_hash, inputs)
        return expected is not None and all(version.get(key) == value for key, value in expected.items())

    @staticmethod
    def _expected_lineage(node_id: str, name: str, source_hash: Any, inputs) -> dict[str, str] | None:
        if not isinstance(source_hash, str) or not source_hash or inputs is None:
            return None
        return {
            'source_hash': source_hash,
            'upstream_data_hash': combine_hashes(
                [source_hash, f'{node_id}/{name}', *[i['artifact_hash'] for i in inputs]]
            ),
            'upstream_code_hash': combine_hashes(
                [source_hash, f'{node_id}/{name}', *[i['upstream_code_hash'] for i in inputs]]
            ),
        }

    def _validate_object(self, metadata: Any) -> str | None:
        if not isinstance(metadata, dict) or not isinstance(metadata.get('artifact_hash'), str):
            return 'incomplete_metadata'
        project = self.project_service.require_project()
        artifact_hash = metadata['artifact_hash']
        record = project.state_db.get_object_record(artifact_hash)
        if record is None or record.get('gc_state') != 'active':
            return 'object_unavailable'
        for key in ('storage_kind', 'data_type', 'size_bytes', 'extension', 'mime_type'):
            if record.get(key) != metadata.get(key):
                return f'object_{key}_mismatch'
        path = project.object_store.object_path(artifact_hash)
        try:
            if not path.is_file() or path.stat().st_size != int(metadata['size_bytes']):
                return 'object_size_mismatch'
            if sha256_bytes(path.read_bytes()) != artifact_hash:
                return 'object_checksum_mismatch'
        except (OSError, TypeError, ValueError):
            return 'object_unavailable'
        return None

    @staticmethod
    def _valid_artifact_version(version: dict[str, Any]) -> bool:
        return all(
            version.get(key) is not None
            for key in (
                'role',
                'artifact_hash',
                'source_hash',
                'upstream_code_hash',
                'upstream_data_hash',
                'run_id',
                'lineage_mode',
                'created_at',
                'warnings',
            )
        )

    @staticmethod
    def _valid_asset_version(version: dict[str, Any]) -> bool:
        return all(
            version.get(key) is not None
            for key in (
                'asset_type',
                'interactive',
                'source_hash',
                'upstream_code_hash',
                'upstream_data_hash',
                'run_id',
                'lineage_mode',
                'definition',
                'modifier_schema',
                'default_modifiers',
                'override_schema_hash',
                'warnings',
                'created_at',
            )
        )

    @staticmethod
    def _matching_artifact_version(connection, incarnation_id: str, entry: dict[str, Any]) -> int | None:
        version_id, expected = entry.get('version_id'), entry['version']
        if not isinstance(version_id, int):
            return None
        row = connection.execute('SELECT * FROM artifact_versions WHERE version_id = ?', (version_id,)).fetchone()
        if row is None or row['incarnation_id'] != incarnation_id:
            return None
        fields = (
            'role',
            'artifact_hash',
            'source_hash',
            'upstream_code_hash',
            'upstream_data_hash',
            'run_id',
            'lineage_mode',
            'created_at',
        )
        if any(row[key] != expected[key] for key in fields) or json.loads(row['warning_json']) != expected['warnings']:
            return None
        return version_id

    @staticmethod
    def _matching_asset_version(connection, incarnation_id: str, entry: dict[str, Any]) -> int | None:
        version_id, expected = entry.get('asset_version_id'), entry['version']
        if not isinstance(version_id, int):
            return None
        row = connection.execute('SELECT * FROM asset_versions WHERE asset_version_id = ?', (version_id,)).fetchone()
        if row is None or row['incarnation_id'] != incarnation_id:
            return None
        scalar = (
            'asset_type',
            'source_hash',
            'upstream_code_hash',
            'upstream_data_hash',
            'run_id',
            'lineage_mode',
            'override_schema_hash',
            'created_at',
        )
        if any(row[key] != expected[key] for key in scalar) or bool(row['interactive']) != bool(
            expected['interactive']
        ):
            return None
        json_fields = (
            ('definition_json', 'definition'),
            ('modifier_schema_json', 'modifier_schema'),
            ('default_modifiers_json', 'default_modifiers'),
            ('warning_json', 'warnings'),
        )
        if any(json.loads(row[column]) != expected[key] for column, key in json_fields):
            return None
        objects = connection.execute(
            'SELECT object_role, object_index, artifact_hash, metadata_json FROM asset_version_objects '
            'WHERE asset_version_id = ? ORDER BY object_role, object_index',
            (version_id,),
        ).fetchall()
        actual = [
            {
                'object_role': row['object_role'],
                'object_index': row['object_index'],
                'artifact_hash': row['artifact_hash'],
                'metadata': None if row['metadata_json'] is None else json.loads(row['metadata_json']),
            }
            for row in objects
        ]
        expected_objects = [
            {key: item.get(key) for key in ('object_role', 'object_index', 'artifact_hash', 'metadata')}
            for item in entry['objects']
        ]
        return version_id if actual == expected_objects else None

    def _set_pending_artifact(self, node, name: str) -> None:
        if not name:
            return
        db = self.project_service.require_project().state_db
        with db._connection() as connection:
            connection.execute(
                'INSERT INTO artifact_heads (node_id, artifact_name, current_version_id, state, incarnation_id) '
                'VALUES (?, ?, NULL, ?, ?) ON CONFLICT(node_id, artifact_name) DO UPDATE SET '
                'current_version_id = NULL, state = excluded.state, incarnation_id = excluded.incarnation_id',
                (node.id, name, ArtifactState.PENDING.value, node.incarnation_id),
            )

    def _set_pending_asset(self, node, name: str) -> None:
        if not name:
            return
        db = self.project_service.require_project().state_db
        with db._connection() as connection:
            connection.execute(
                'INSERT INTO asset_heads (node_id, asset_name, current_asset_version_id, state, incarnation_id) '
                'VALUES (?, ?, NULL, ?, ?) ON CONFLICT(node_id, asset_name) DO UPDATE SET '
                'current_asset_version_id = NULL, state = excluded.state, incarnation_id = excluded.incarnation_id',
                (node.id, name, ArtifactState.PENDING.value, node.incarnation_id),
            )

    def _notice_unavailable(self, checkpoint_id: str, node_id: str, kind: str, name: str, reason: str) -> None:
        self._notice(
            checkpoint_id,
            node_id,
            f'checkpoint_{kind}_unavailable',
            f'Checkpoint {kind} `{node_id}/{name}` could not be restored.',
            {'checkpoint_id': checkpoint_id, 'kind': kind, 'name': name, 'reason': reason},
        )

    def _notice(
        self, checkpoint_id: str, node_id: str | None, code: str, message: str, details: dict[str, Any]
    ) -> None:
        issue_id = f'{code}:{hash_json([checkpoint_id, node_id, details])[:24]}'
        self.project_service.record_notice(
            issue_id=issue_id,
            node_id=node_id,
            severity=ValidationSeverity.WARNING,
            code=code,
            message=message,
            details=details,
        )

    def _drop_state_for_missing_nodes(self) -> None:
        project = self.project_service.require_project()
        current_node_ids = {node.id for node in self.project_service.graph().nodes}
        for node_id in project.state_db.list_state_node_ids():
            if node_id not in current_node_ids:
                project.state_db.delete_node_state(node_id)

    def _mark_restored_notebooks_stale(self) -> None:
        from bulletjournal.services.graph_service import GraphService

        notebook_ids = [node.id for node in self.project_service.graph().nodes if node.kind == NodeKind.NOTEBOOK]
        if notebook_ids:
            GraphService(self.project_service).mark_nodes_and_downstream_stale(notebook_ids)

    def _reconcile_artifact_state(self) -> None:
        project = self.project_service.require_project()
        allowed_artifacts: dict[str, set[str]] = {}
        for node in self.project_service.graph().nodes:
            if node.kind == NodeKind.FILE_INPUT:
                artifact_name = file_input_artifact_name(node)
                allowed_artifacts[node.id] = {artifact_name}
                project.state_db.ensure_artifact_head(node.id, artifact_name, ArtifactState.PENDING)
                continue
            if node.kind in {NodeKind.ORGANIZER, NodeKind.AREA, NodeKind.DASHBOARD}:
                allowed_artifacts[node.id] = set()
                continue
            interface = self.project_service.latest_interface(node.id)
            if interface is None:
                allowed_artifacts[node.id] = set()
                continue
            names = {str(port['name']) for port in interface.get('outputs', [])}
            allowed_artifacts[node.id] = names
            for artifact_name in names:
                project.state_db.ensure_artifact_head(node.id, artifact_name, ArtifactState.PENDING)
        for head in project.state_db.list_artifact_heads():
            node_id = str(head['node_id'])
            artifact_name = str(head['artifact_name'])
            if artifact_name not in allowed_artifacts.get(node_id, set()):
                project.state_db.delete_artifact_state(node_id, artifact_name)

    def _checkpoint_is_due(self, created_at: str) -> bool:
        normalized = created_at.replace('Z', '+00:00')
        return datetime.now(tz=UTC) - datetime.fromisoformat(normalized) >= AUTO_CHECKPOINT_INTERVAL

    def _next_checkpoint_id(self) -> str:
        base_id = utc_now_iso().replace(':', '-')
        checkpoints = self.project_service.require_project().state_db.list_checkpoints()
        existing_ids = {checkpoint.checkpoint_id for checkpoint in checkpoints}
        checkpoint_id = base_id
        suffix = 1
        while checkpoint_id in existing_ids:
            checkpoint_id = f'{base_id}-{suffix}'
            suffix += 1
        return checkpoint_id
