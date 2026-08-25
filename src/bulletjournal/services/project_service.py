from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.graph_bindings import organizer_interface_for_node, resolve_input_binding
from bulletjournal.domain.hashing import combine_hashes
from bulletjournal.domain.models import (
    GraphData,
    Node,
    NotebookInterface,
    Port,
    ProjectMetadata,
    constant_artifact_name,
    constant_data_type,
    file_input_artifact_name,
)
from bulletjournal.domain.state_machine import derive_node_state
from bulletjournal.execution.planner import downstream_closure, upstream_closure
from bulletjournal.execution.watcher import NotebookWatcher
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.services.notebook_freshness import lineage_metadata_for_notebook, notebook_uses_execution_head
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_gc import ObjectGarbageCollector, ObjectGCSettings
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root, load_project_json, require_project_root
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.storage.state_db import StateDB
from bulletjournal.utils import utc_now_iso

_MARKDOWN_CODE_SPAN_PATTERN = re.compile(r'(`[^`]*`)')
_MARKDOWN_VALUE_PATTERN = re.compile(r'(^|[^A-Za-z0-9`])([A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)+)(?=$|[^A-Za-z0-9`])')


def _format_markdown_code(value: str) -> str:
    sanitized = value.replace('`', "'")
    return f'`{sanitized}`'


def _auto_format_markdown_values(text: str) -> str:
    parts = _MARKDOWN_CODE_SPAN_PATTERN.split(text)
    formatted: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith('`') and part.endswith('`'):
            formatted.append(part)
            continue
        formatted.append(
            _MARKDOWN_VALUE_PATTERN.sub(
                lambda match: f'{match.group(1)}{_format_markdown_code(match.group(2))}',
                part,
            )
        )
    return ''.join(formatted)


def _describe_node_label(node: Node) -> str:
    if node.title == node.id:
        return _format_markdown_code(node.id)
    return f'{_format_markdown_code(node.title)} ({_format_markdown_code(node.id)})'


@dataclass(slots=True)
class OpenProject:
    paths: ProjectPaths
    metadata: ProjectMetadata
    graph_store: GraphStore
    state_db: StateDB
    object_store: ObjectStore


class ProjectService:
    def __init__(self, event_service, template_service) -> None:
        self.event_service = event_service
        self.template_service = template_service
        self.dashboard_service = None
        self.checkpoint_service = None
        self.project: OpenProject | None = None
        self.run_service = None
        self.watcher = NotebookWatcher(self)
        self._automatic_checkpoint_suspensions = 0
        self._gc_task_lock = Lock()
        self._gc_task_active = False

    def init_project(self, path: Path, *, title: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        paths = init_project_root(path, title=title, project_id=project_id)
        self._open_paths(paths)
        self.reparse_all_notebooks()
        return self.snapshot()

    def open_project(self, path: Path) -> dict[str, Any]:
        self._open_paths(require_project_root(path))
        self.reparse_all_notebooks()
        return self.snapshot()

    def _open_paths(self, paths: ProjectPaths) -> OpenProject:
        if self.project is not None and self.project.paths.root != paths.root:
            raise InvalidRequestError('This process is already bound to a different project root.')
        project_json = load_project_json(paths)
        metadata = ProjectMetadata(
            project_id=_as_str(project_json['project_id']),
            created_at=_as_str(project_json['created_at']),
            title=_optional_str(project_json.get('title')),
        )
        graph_store = GraphStore(paths)
        state_db = StateDB(paths.state_db_path)
        graph = graph_store.ensure_incarnations()
        state_db.reconcile_node_incarnations(graph.nodes)
        object_store = ObjectStore(paths)
        project = OpenProject(
            paths=paths, metadata=metadata, graph_store=graph_store, state_db=state_db, object_store=object_store
        )
        state_db.abort_inflight_runs()
        self.project = project
        ObjectGarbageCollector(paths).recover()
        self._reconcile_startup_heads(graph)
        self.watcher.start()
        self._ensure_activity_meta(project, graph.meta.get('updated_at'))
        self.event_service.publish(
            'project.opened',
            project_id=metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={'project_id': metadata.project_id, 'root': str(paths.root)},
        )
        return project

    def _reconcile_startup_heads(self, graph: GraphData) -> None:
        project = self.require_project()
        with ProjectLock(project.paths.project_lock_path).exclusive():
            for node in graph.nodes:
                try:
                    self._reconcile_startup_node(node, graph)
                except Exception as exc:  # A damaged node must not prevent the project from opening.
                    with suppress(Exception):
                        project.state_db.save_persistent_notice(
                            issue_id=f'startup_reconciliation:{node.incarnation_id}',
                            node_id=node.id,
                            severity=ValidationSeverity.ERROR,
                            code='startup_reconciliation_failed',
                            message=f'Could not verify saved outputs for `{node.id}` during startup.',
                            details={'error': str(exc)},
                        )

    def _reconcile_startup_node(self, node: Node, graph: GraphData) -> None:
        project = self.require_project()
        db = project.state_db
        source_hash: str | None = None
        source_valid = node.kind != NodeKind.NOTEBOOK
        interface = self.latest_interface(node.id)
        if node.kind == NodeKind.NOTEBOOK:
            try:
                path = project.paths.notebook_path(node.id)
                source_hash = normalized_source_hash_text(path.read_text(encoding='utf-8'))
                source_valid = bool(interface and interface.get('source_hash') == source_hash)
            except (OSError, UnicodeError):
                source_valid = False

        input_lineage = self._startup_input_lineage(node.id, interface, graph) if source_valid else None
        for head in db.list_artifact_heads():
            if head['node_id'] != node.id or head.get('current_version_id') is None:
                continue
            reason = None
            if not self._startup_head_version_belongs_to_node(node, 'artifact', head['current_version_id']):
                reason = 'invalid_incarnation'
            if reason is None:
                reason = self._invalid_startup_object(head.get('artifact_hash'), head.get('size_bytes'))
            if reason is not None:
                self._clear_startup_head(
                    node, 'artifact', str(head['artifact_name']), reason, head.get('artifact_hash')
                )
            elif node.kind == NodeKind.NOTEBOOK and (
                not source_valid
                or not self._startup_lineage_matches(
                    head, source_hash, f'{node.id}/{head["artifact_name"]}', input_lineage
                )
            ):
                db.set_artifact_head_state(node.id, str(head['artifact_name']), ArtifactState.STALE)

        for head in db.list_asset_heads(node_id=node.id):
            if head.get('current_asset_version_id') is None:
                continue
            invalid = (
                (
                    None,
                    'invalid_incarnation',
                )
                if not self._startup_head_version_belongs_to_node(node, 'asset', head['current_asset_version_id'])
                else None
            )
            if invalid is None:
                for item in head.get('objects', []):
                    reason = self._invalid_startup_object_for_hash(item.get('artifact_hash'))
                    if reason is not None:
                        invalid = (item.get('artifact_hash'), reason)
                        break
            if invalid is not None:
                self._clear_startup_head(node, 'asset', str(head['asset_name']), invalid[1], invalid[0])
            elif node.kind == NodeKind.NOTEBOOK and (
                not source_valid
                or not self._startup_lineage_matches(
                    head, source_hash, f'{node.id}/{head["asset_name"]}', input_lineage
                )
            ):
                db.set_asset_head_state(node.id, str(head['asset_name']), ArtifactState.STALE)

        execution = db.get_notebook_execution_head(node.id)
        if execution is not None and node.kind == NodeKind.NOTEBOOK:
            execution_lineage = lineage_metadata_for_notebook(self, node.id, graph) if source_valid else None
            if (
                not source_valid
                or execution_lineage is None
                or any(
                    execution.get(key) != execution_lineage[key]
                    for key in ('source_hash', 'upstream_data_hash', 'upstream_code_hash')
                )
            ):
                state = ArtifactState.STALE if execution.get('last_run_finished_at') else ArtifactState.PENDING
                db.set_notebook_execution_head_state(node.id, state)

    def _startup_head_version_belongs_to_node(self, node: Node, kind: str, version_id: object) -> bool:
        if not isinstance(version_id, int):
            return False
        table, column = (
            ('artifact_versions', 'version_id') if kind == 'artifact' else ('asset_versions', 'asset_version_id')
        )
        with self.require_project().state_db._connection() as connection:
            row = connection.execute(
                f'SELECT node_id, incarnation_id FROM {table} WHERE {column} = ?',  # noqa: S608
                (version_id,),
            ).fetchone()
        return bool(row is not None and row['node_id'] == node.id and row['incarnation_id'] == node.incarnation_id)

    def _startup_input_lineage(
        self, node_id: str, interface: dict[str, Any] | None, graph: GraphData
    ) -> tuple[list[str], list[str]] | None:
        if interface is None:
            return None
        data_hashes: list[str] = []
        code_hashes: list[str] = []
        db = self.require_project().state_db
        for port in interface.get('inputs', []):
            binding = resolve_input_binding(graph, node_id=node_id, input_name=str(port['name']))
            if binding is None:
                if not port.get('has_default'):
                    return None
                from bulletjournal.domain.hashing import hash_json

                data_hashes.append(hash_json(port.get('default')))
                code_hashes.append('default')
                continue
            head = db.get_artifact_head(*binding)
            if head is None or head.get('current_version_id') is None or head.get('state') != ArtifactState.READY.value:
                return None
            if not head.get('artifact_hash') or not head.get('upstream_code_hash'):
                return None
            data_hashes.append(str(head['artifact_hash']))
            code_hashes.append(str(head['upstream_code_hash']))
        return data_hashes, code_hashes

    @staticmethod
    def _startup_lineage_matches(
        head: dict[str, Any],
        source_hash: str | None,
        logical_output: str,
        input_lineage: tuple[list[str], list[str]] | None,
    ) -> bool:
        return bool(
            source_hash
            and input_lineage is not None
            and head.get('source_hash') == source_hash
            and head.get('upstream_data_hash') == combine_hashes([source_hash, logical_output, *input_lineage[0]])
            and head.get('upstream_code_hash') == combine_hashes([source_hash, logical_output, *input_lineage[1]])
        )

    def _invalid_startup_object_for_hash(self, artifact_hash: object) -> str | None:
        if not isinstance(artifact_hash, str) or not artifact_hash:
            return 'missing_object_reference'
        record = self.require_project().state_db.get_object_record(artifact_hash)
        if record is None:
            return 'missing_object_row'
        return self._invalid_startup_object(artifact_hash, record.get('size_bytes'))

    def _invalid_startup_object(self, artifact_hash: object, size_bytes: object) -> str | None:
        if not isinstance(artifact_hash, str) or not artifact_hash:
            return 'missing_object_reference'
        record = self.require_project().state_db.get_object_record(artifact_hash)
        if record is None:
            return 'missing_object_row'
        if record.get('gc_state') != 'active':
            return 'object_not_active'
        if not isinstance(size_bytes, int) or int(record['size_bytes']) != size_bytes:
            return 'invalid_object_metadata'
        try:
            self.require_project().object_store.verify_object(artifact_hash, size_bytes)
        except FileNotFoundError:
            return 'missing_canonical_file'
        except (OSError, ValueError):
            self._quarantine_corrupt_startup_object(artifact_hash)
            return 'corrupt_canonical_file'
        return None

    def _quarantine_corrupt_startup_object(self, artifact_hash: str) -> None:
        store = self.require_project().object_store
        source = store.object_path(artifact_hash)
        destination = store.quarantine_path(artifact_hash)
        if not source.is_file() or destination.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        db = self.require_project().state_db
        with db._connection() as connection:
            connection.execute(
                "UPDATE objects SET gc_state = 'quarantined', quarantined_at = ?, quarantine_path = ? "
                'WHERE artifact_hash = ?',
                (utc_now_iso(), str(destination.relative_to(store.paths.root)), artifact_hash),
            )
            connection.commit()

    def _clear_startup_head(self, node: Node, kind: str, name: str, reason: str, artifact_hash: object) -> None:
        db = self.require_project().state_db
        table, version_column, name_column = (
            ('artifact_heads', 'current_version_id', 'artifact_name')
            if kind == 'artifact'
            else ('asset_heads', 'current_asset_version_id', 'asset_name')
        )
        with db._connection() as connection:
            connection.execute(
                f'UPDATE {table} SET {version_column} = NULL, state = ? '  # noqa: S608 - fixed table allowlist.
                f'WHERE node_id = ? AND {name_column} = ? AND incarnation_id = ?',
                (ArtifactState.PENDING.value, node.id, name, node.incarnation_id),
            )
            connection.commit()
        db.save_persistent_notice(
            issue_id=f'object_integrity:{node.incarnation_id}:{kind}:{name}',
            node_id=node.id,
            severity=ValidationSeverity.ERROR,
            code='object_integrity_failed',
            message=f'Saved {kind} `{node.id}/{name}` is unavailable and must be rebuilt.',
            details={'kind': kind, 'name': name, 'artifact_hash': artifact_hash, 'reason': reason},
        )

    def require_project(self) -> OpenProject:
        if self.project is None:
            raise InvalidRequestError('No project is currently open.')
        return self.project

    def graph(self) -> GraphData:
        return self.require_project().graph_store.read()

    def write_graph(self, graph: GraphData, *, increment_version: bool = True) -> GraphData:
        project = self.require_project()
        graph = project.graph_store.write(graph, increment_version=increment_version)
        self.record_graph_activity(str(graph.meta.get('updated_at') or utc_now_iso()))
        self.event_service.publish(
            'graph.updated',
            project_id=project.metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={'graph_version': graph.meta['graph_version']},
        )
        return graph

    def list_nodes(self) -> list[Node]:
        return self.graph().nodes

    def get_node(self, node_id: str) -> Node:
        for node in self.graph().nodes:
            if node.id == node_id:
                return node
        raise NotFoundError(f'Unknown node `{node_id}`.')

    def notebook_path(self, node_id: str) -> Path:
        return self.require_project().paths.notebook_path(node_id)

    def latest_interface(self, node_id: str, *, include_dismissed: bool = False) -> dict[str, Any] | None:
        node = self.get_node(node_id)
        if node.kind == NodeKind.FILE_INPUT:
            return self.synthetic_file_input_interface(node).to_dict()
        if node.kind == NodeKind.CONSTANT:
            return self.synthetic_constant_interface(node).to_dict()
        if node.kind == NodeKind.ORGANIZER:
            return organizer_interface_for_node(node).to_dict()
        if node.kind == NodeKind.AREA:
            return None
        if node.kind == NodeKind.DASHBOARD:
            return None
        interface = self.require_project().state_db.latest_interface_json(node_id)
        if interface is None:
            return None
        resolved = dict(interface)
        resolved['issues'] = self.validation_issues(node_id=node_id, include_dismissed=include_dismissed)
        return resolved

    def interfaces_by_node(self) -> dict[str, dict[str, Any]]:
        interfaces: dict[str, dict[str, Any]] = {}
        for node in self.graph().nodes:
            interface = self.latest_interface(node.id)
            if interface is not None:
                interfaces[node.id] = dict(interface)
        return interfaces

    def synthetic_file_input_interface(self, node: Node) -> NotebookInterface:
        artifact_name = file_input_artifact_name(node)
        return NotebookInterface(
            node_id=node.id,
            source_hash='file_input',
            inputs=[],
            outputs=[
                Port(
                    name=artifact_name,
                    data_type='file',
                    role=ArtifactRole.OUTPUT,
                    description='Uploaded file',
                    kind='file',
                    direction='output',
                    declaration_index=0,
                )
            ],
            docs='File input node.',
            issues=[],
        )

    def synthetic_constant_interface(self, node: Node) -> NotebookInterface:
        artifact_name = constant_artifact_name(node)
        data_type = constant_data_type(node)
        return NotebookInterface(
            node_id=node.id,
            source_hash=f'constant:{data_type}',
            inputs=[],
            outputs=[
                Port(
                    name=artifact_name,
                    data_type=data_type,
                    role=ArtifactRole.OUTPUT,
                    description='Constant artifact',
                    kind='file' if data_type == 'file' else 'value',
                    direction='output',
                    declaration_index=0,
                )
            ],
            docs='Constant block.',
            issues=[],
        )

    def validation_issues(self, *, node_id: str | None = None, include_dismissed: bool = False) -> list[dict[str, Any]]:
        return self.require_project().state_db.list_validation_issues(
            node_id=node_id, include_dismissed=include_dismissed
        )

    def notices(self) -> list[dict[str, Any]]:
        notices = [
            *self.validation_issues(),
            *self.require_project().state_db.list_persistent_notices(),
        ]
        return sorted(notices, key=_notice_sort_key)

    def dismiss_notice(self, issue_id: str) -> dict[str, Any]:
        project = self.require_project()
        issue = project.state_db.get_validation_issue(issue_id)
        if issue is not None:
            project.state_db.dismiss_validation_issue(issue_id)
        else:
            issue = project.state_db.get_persistent_notice(issue_id)
            if issue is None:
                raise NotFoundError(f'Unknown notice `{issue_id}`.')
            project.state_db.dismiss_persistent_notice(issue_id)
        graph_version = int(self.graph().meta['graph_version'])
        self.event_service.publish(
            'notice.dismissed',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={'issue_id': issue_id},
        )
        return {'issue_id': issue_id, 'status': 'dismissed'}

    def record_notice(
        self,
        *,
        issue_id: str,
        node_id: str | None,
        severity: ValidationSeverity,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.require_project()
        resolved_details = details or {}
        formatted_message = _auto_format_markdown_values(message)
        project.state_db.save_persistent_notice(
            issue_id=issue_id,
            node_id=node_id,
            severity=severity,
            code=code,
            message=formatted_message,
            details=resolved_details,
        )
        notice = project.state_db.get_persistent_notice(issue_id)
        graph_version = int(self.graph().meta['graph_version'])
        self.event_service.publish(
            'notice.created',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={
                'issue_id': issue_id,
                'node_id': node_id,
                'severity': severity.value,
                'code': code,
                'message': formatted_message,
                'details': resolved_details,
            },
        )
        if notice is None:
            raise RuntimeError(f'Failed to load persisted notice `{issue_id}`.')
        return notice

    def record_undefined_constant_notice(self, node: Node) -> None:
        incarnation = self.require_project().state_db.live_incarnation(node.id)
        if incarnation is None:
            raise NotFoundError(f'Node `{node.id}` has no live incarnation.')
        self.record_notice(
            issue_id=f'constant_undefined:{node.incarnation_id}:{incarnation["generation"]}',
            node_id=node.id,
            severity=ValidationSeverity.WARNING,
            code='constant_undefined',
            message=f'Constant {_format_markdown_code(node.title)} is undefined.',
            details={'node_id': node.id},
        )

    def dismiss_undefined_constant_notice(self, node_id: str) -> None:
        self.require_project().state_db.dismiss_persistent_notices_for_node(node_id, ['constant_undefined'])

    def block_is_frozen(self, node: Node) -> bool:
        return bool(node.ui.get('frozen'))

    def frozen_block_blockers_for_stale_roots(
        self,
        node_ids: list[str],
        *,
        graph: GraphData | None = None,
    ) -> list[Node]:
        resolved_graph = self.graph() if graph is None else graph
        affected: set[str] = set(node_ids)
        for node_id in node_ids:
            affected.update(downstream_closure(resolved_graph, node_id))
        return [node for node in resolved_graph.nodes if node.id in affected and self.block_is_frozen(node)]

    def frozen_block_blockers_for_node_edit(
        self,
        node_id: str,
        *,
        graph: GraphData | None = None,
    ) -> list[Node]:
        return self.frozen_block_blockers_for_stale_roots([node_id], graph=graph)

    @staticmethod
    def freeze_block_message(blockers: list[Node]) -> str:
        labels = ', '.join(_describe_node_label(node) for node in blockers)
        if len(blockers) == 1:
            return f'This change is blocked because it would affect the frozen block {labels}. Unfreeze it first.'
        return f'This change is blocked because it would affect frozen blocks {labels}. Unfreeze them first.'

    def freeze_targets_for_node(
        self,
        node_id: str,
        *,
        graph: GraphData | None = None,
    ) -> list[Node]:
        resolved_graph = self.graph() if graph is None else graph
        target = next((node for node in resolved_graph.nodes if node.id == node_id), None)
        if target is None:
            raise NotFoundError(f'Unknown node `{node_id}`.')
        target_ids = set(upstream_closure(resolved_graph, node_id)) | {node_id}
        return [node for node in resolved_graph.nodes if node.id in target_ids]

    def active_editor_upstream_blockers_for_freeze(
        self,
        node_id: str,
        *,
        graph: GraphData | None = None,
    ) -> list[Node]:
        if self.run_service is None:
            return []
        resolved_graph = self.graph() if graph is None else graph
        upstream_ids = set(upstream_closure(resolved_graph, node_id))
        if not upstream_ids:
            return []
        blockers: list[Node] = []
        for node in resolved_graph.nodes:
            if node.id not in upstream_ids or node.kind != NodeKind.NOTEBOOK:
                continue
            if self.run_service.session_manager.get_by_node(node.id) is not None:
                blockers.append(node)
        return blockers

    @staticmethod
    def freeze_upstream_editor_block_message(blockers: list[Node]) -> str:
        labels = ', '.join(_describe_node_label(node) for node in blockers)
        if len(blockers) == 1:
            return f'Freeze is blocked because an upstream editor is open for {labels}. Close it first.'
        return f'Freeze is blocked because upstream editors are open for {labels}. Close them first.'

    def snapshot(self) -> dict[str, Any]:
        project = self.require_project()
        graph = project.graph_store.read()
        interfaces = self.interfaces_by_node()
        validation = self.validation_issues()
        validation_errors_by_node: dict[str, bool] = {}
        for issue in validation:
            if issue.get('severity') == ValidationSeverity.ERROR.value:
                validation_errors_by_node[str(issue['node_id'])] = True
        notices = self.notices()
        runtime_error_notice_by_node: dict[str, bool] = {}
        for notice in notices:
            node_id = notice.get('node_id')
            if notice.get('code') != 'run_failed' or not isinstance(node_id, str) or not node_id:
                continue
            runtime_error_notice_by_node[node_id] = True
        artifacts = project.state_db.list_artifact_heads()
        notebook_execution_heads = {
            str(head['node_id']): head for head in project.state_db.list_notebook_execution_heads()
        }
        artifact_states_by_node: dict[str, list[str]] = {}
        for artifact in artifacts:
            artifact_states_by_node.setdefault(str(artifact['node_id']), []).append(str(artifact['state']))
        asset_states_by_node: dict[str, list[str]] = {}
        for asset in project.state_db.list_asset_heads():
            asset_states_by_node.setdefault(str(asset['node_id']), []).append(str(asset['state']))
        runs = project.state_db.list_run_records()
        execution_meta_by_node = project.state_db.list_orchestrator_execution_meta()
        orchestrator_state_by_node = self.run_service.orchestrator_state() if self.run_service is not None else {}
        node_payload = []
        for node in graph.nodes:
            interface = interfaces.get(node.id)
            template_status = None
            resolved_template = node.template
            if node.template is not None:
                try:
                    resolved_template = self.template_service.template_ref(node.template.ref)
                except FileNotFoundError:
                    resolved_template = node.template
            if resolved_template is not None and resolved_template.ref == 'builtin/empty_notebook':
                resolved_template = None
            if resolved_template and interface is not None and node.kind == NodeKind.NOTEBOOK:
                try:
                    template_source = self.template_service.resolve_template_source(resolved_template.ref)
                    rendered_template_source = self.template_service.render_template_source(
                        template_source,
                        node_id=node.id,
                    )
                    expected_source_hash = normalized_source_hash_text(rendered_template_source)
                    template_status = 'template' if interface.get('source_hash') == expected_source_hash else 'modified'
                except FileNotFoundError:
                    project.state_db.save_persistent_notice(
                        issue_id=f'missing_notebook_template:{node.incarnation_id}',
                        node_id=node.id,
                        severity=ValidationSeverity.WARNING,
                        code='missing_notebook_template',
                        message=f'Notebook template `{resolved_template.ref}` is no longer available.',
                        details={'template_ref': resolved_template.ref},
                    )
            node_ui = dict(node.ui)
            if node.kind == NodeKind.NOTEBOOK:
                asset_states = asset_states_by_node.get(node.id, [])
                node_ui['asset_counts'] = {
                    ArtifactState.PENDING.value: asset_states.count(ArtifactState.PENDING.value),
                    ArtifactState.STALE.value: asset_states.count(ArtifactState.STALE.value),
                    ArtifactState.READY.value: asset_states.count(ArtifactState.READY.value),
                }
            if node.kind == NodeKind.DASHBOARD and self.dashboard_service is not None:
                node_ui = self._dashboard_ui_payload(node.id, base_ui=node_ui)
            orchestrator_state = orchestrator_state_by_node.get(node.id)
            output_states = [
                *artifact_states_by_node.get(node.id, []),
                *asset_states_by_node.get(node.id, []),
            ]
            if node.kind == NodeKind.NOTEBOOK and notebook_uses_execution_head(self, node.id, interface):
                execution_head = notebook_execution_heads.get(node.id)
                if execution_head is None:
                    output_states = [ArtifactState.PENDING.value]
                else:
                    output_states = [str(execution_head['state'])]
            node_payload.append(
                {
                    **{**node.to_dict(), 'ui': node_ui},
                    'template': resolved_template.to_dict() if resolved_template else None,
                    'interface': interface,
                    'template_status': template_status,
                    'execution_meta': execution_meta_by_node.get(node.id),
                    'orchestrator_state': orchestrator_state,
                    'state': derive_node_state(
                        output_states,
                        run_failed=runtime_error_notice_by_node.get(node.id, False),
                        validation_failed=validation_errors_by_node.get(node.id, False),
                    ),
                }
            )
        notices = self.notices()
        return {
            'server_time': utc_now_iso(),
            'project': {
                'project_id': project.metadata.project_id,
                'title': project.metadata.title,
                'created_at': project.metadata.created_at,
                'root': str(project.paths.root),
                'project_root': str(project.paths.root),
            },
            'graph': {
                'meta': graph.meta,
                'nodes': node_payload,
                'edges': [edge.to_dict() for edge in graph.edges],
                'layout': [entry.to_dict() for entry in graph.layout],
            },
            'validation_issues': validation,
            'notices': notices,
            'artifacts': artifacts,
            'runs': runs,
            'checkpoints': [asdict(checkpoint) for checkpoint in project.state_db.list_checkpoints()],
            'templates': self.template_service.list_templates(),
        }

    def project_metadata_payload(self) -> dict[str, Any]:
        project = self.require_project()
        return {
            'project_id': project.metadata.project_id,
            'created_at': project.metadata.created_at,
            'root': str(project.paths.root),
            'project_root': str(project.paths.root),
            'title': project.metadata.title,
        }

    def project_status(self) -> dict[str, Any]:
        project = self.require_project()
        meta = project.state_db.list_project_meta()
        has_active_run = bool(self.run_service.has_active_run()) if self.run_service is not None else False
        last_graph_edit_at = meta.get('last_graph_edit_at')
        last_notebook_edit_at = meta.get('last_notebook_edit_at')
        last_run_started_at = project.state_db.latest_run_started_at()
        last_run_finished_at = project.state_db.latest_run_finished_at()
        relevant = [
            timestamp
            for timestamp in [last_graph_edit_at, last_notebook_edit_at, last_run_finished_at, last_run_started_at]
            if timestamp
        ]
        idle_since = max(relevant) if relevant else project.metadata.created_at
        idle_eligible = not has_active_run
        return {
            'project_id': project.metadata.project_id,
            'server_status': 'ok',
            'has_active_run': has_active_run,
            'last_graph_edit_at': last_graph_edit_at,
            'last_notebook_edit_at': last_notebook_edit_at,
            'last_run_started_at': last_run_started_at,
            'last_run_finished_at': last_run_finished_at,
            'idle_shutdown_eligible': idle_eligible,
            'idle_shutdown_eligible_since': idle_since if idle_eligible else None,
        }

    def record_graph_activity(self, timestamp: str | None = None) -> None:
        self.require_project().state_db.set_project_meta('last_graph_edit_at', timestamp or utc_now_iso())
        self.request_gc()

    def record_notebook_activity(self, timestamp: str | None = None) -> None:
        self.require_project().state_db.set_project_meta('last_notebook_edit_at', timestamp or utc_now_iso())
        self.request_gc()

    def gc_status(self) -> dict[str, Any]:
        project = self.require_project()
        meta = project.state_db.list_project_meta()
        settings = ObjectGCSettings.from_project_meta(project.state_db)
        report = json.loads(meta['gc_last_report']) if meta.get('gc_last_report') else None
        return {
            'task_active': self._gc_task_active,
            'requested_at': meta.get('gc_requested_at'),
            'last_completed_at': meta.get('gc_last_completed_at'),
            'last_report': report,
            'settings': asdict(settings),
        }

    def collect_garbage(self, *, dry_run: bool = True) -> dict[str, Any]:
        project = self.require_project()
        collector = ObjectGarbageCollector(project.paths, activity_check=self._gc_activity_blocker)
        report = collector.collect(dry_run=dry_run)
        payload = report.as_dict()
        project.state_db.set_project_meta('gc_last_report', json.dumps(payload, sort_keys=True))
        return payload

    def request_gc(self) -> bool:
        project = self.require_project()
        settings = ObjectGCSettings.from_project_meta(project.state_db)
        if not settings.enabled:
            return False
        now = datetime.now(tz=UTC)
        project.state_db.set_project_meta('gc_requested_at', now.isoformat().replace('+00:00', 'Z'))
        last = project.state_db.get_project_meta('gc_last_completed_at')
        minimum = settings.min_interval_seconds
        if last:
            try:
                if (now - datetime.fromisoformat(last.replace('Z', '+00:00'))).total_seconds() < minimum:
                    return False
            except ValueError:
                return False
        with self._gc_task_lock:
            if self._gc_task_active:
                return False
            self._gc_task_active = True
        Thread(target=self._run_scheduled_gc, name='bulletjournal-gc', daemon=True).start()
        return True

    def _run_scheduled_gc(self) -> None:
        try:
            self.collect_garbage(dry_run=False)
        except Exception as exc:
            project = self.project
            if project is not None:
                project.state_db.set_project_meta('gc_last_error', str(exc))
        finally:
            with self._gc_task_lock:
                self._gc_task_active = False

    def _gc_activity_blocker(self) -> str | None:
        if self.run_service is None:
            return None
        if self.run_service.has_active_run():
            return 'active_execution'
        if self.run_service.session_manager.list():
            return 'active_editor'
        return None

    @property
    def automatic_checkpoints_suspended(self) -> bool:
        return self._automatic_checkpoint_suspensions > 0

    @contextmanager
    def suspend_automatic_checkpoints(self):
        self._automatic_checkpoint_suspensions += 1
        try:
            yield
        finally:
            self._automatic_checkpoint_suspensions -= 1

    def create_automatic_checkpoint_if_due(self) -> dict[str, object] | None:
        if self.checkpoint_service is None or self.automatic_checkpoints_suspended:
            return None
        return self.checkpoint_service.create_automatic_checkpoint_if_due()

    def mark_environment_changed(self, *, reason: str, mark_all_artifacts_stale: bool = True) -> dict[str, Any]:
        project = self.require_project()
        notice_id = f'environment_changed:{uuid.uuid4()}'
        stale_count = 0
        frozen_node_ids: set[str] = set()
        if mark_all_artifacts_stale:
            graph = self.graph()
            notebook_ids = {node.id for node in graph.nodes if node.kind == NodeKind.NOTEBOOK}
            frozen_ids = {node.id for node in graph.nodes if self.block_is_frozen(node)}
            for head in project.state_db.list_artifact_heads():
                if head['node_id'] not in notebook_ids:
                    continue
                if head['current_version_id'] is None or head['state'] == ArtifactState.STALE.value:
                    continue
                if head['node_id'] in frozen_ids:
                    frozen_node_ids.add(str(head['node_id']))
                    continue
                project.state_db.set_artifact_head_state(head['node_id'], head['artifact_name'], ArtifactState.STALE)
                stale_count += 1
            for head in project.state_db.list_asset_heads():
                if head['node_id'] not in notebook_ids:
                    continue
                if head['current_asset_version_id'] is None or head['state'] == ArtifactState.STALE.value:
                    continue
                if head['node_id'] in frozen_ids:
                    frozen_node_ids.add(str(head['node_id']))
                    continue
                project.state_db.set_asset_head_state(head['node_id'], head['asset_name'], ArtifactState.STALE)
                stale_count += 1
            graph_version = int(graph.meta['graph_version'])
            self.event_service.publish(
                'project.environment_changed',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'reason': reason, 'mark_all_artifacts_stale': True, 'stale_count': stale_count},
            )
        notice = self.record_notice(
            issue_id=notice_id,
            node_id=None,
            severity=ValidationSeverity.WARNING,
            code='environment_changed',
            message='Project outputs were marked stale because the environment changed.',
            details={'reason': reason, 'mark_all_artifacts_stale': mark_all_artifacts_stale},
        )
        frozen_notice = None
        if frozen_node_ids:
            frozen_notice = self.record_notice(
                issue_id=f'{notice_id}:frozen_blocks',
                node_id=None,
                severity=ValidationSeverity.WARNING,
                code='environment_changed_frozen_blocks',
                message='Outputs for frozen blocks were not marked stale because the environment changed.',
                details={
                    'reason': reason,
                    'node_ids': sorted(frozen_node_ids),
                    'skipped_node_count': len(frozen_node_ids),
                },
            )
        return {
            'project_id': project.metadata.project_id,
            'reason': reason,
            'mark_all_artifacts_stale': mark_all_artifacts_stale,
            'stale_count': stale_count,
            'notice': notice,
            'frozen_notice': frozen_notice,
        }

    def reparse_all_notebooks(self) -> None:
        project = self.require_project()
        graph = project.graph_store.read()
        from bulletjournal.services.notebook_service import NotebookService  # local import to avoid cycle

        notebook_service = NotebookService(self)
        for node in graph.nodes:
            if node.kind == NodeKind.FILE_INPUT:
                project.state_db.ensure_artifact_head(node.id, file_input_artifact_name(node), ArtifactState.PENDING)
                continue
            if node.kind == NodeKind.CONSTANT:
                project.state_db.ensure_artifact_head(node.id, constant_artifact_name(node), ArtifactState.PENDING)
                continue
            if node.kind in {NodeKind.ORGANIZER, NodeKind.AREA, NodeKind.DASHBOARD}:
                continue
            try:
                notebook_service.reparse_notebook(node.id)
            except (FileNotFoundError, OSError, UnicodeError):
                # Startup reconciliation has already retained the graph and made prior outputs non-ready.
                continue

    def reparse_notebook_by_path(self, path: Path) -> None:
        node_id = path.stem
        try:
            node = self.get_node(node_id)
        except NotFoundError:
            return
        if node.kind != NodeKind.NOTEBOOK:
            return
        from bulletjournal.services.notebook_service import NotebookService  # local import to avoid cycle

        NotebookService(self).reparse_notebook(node_id)

    def stop(self) -> None:
        self.watcher.stop()

    def _ensure_activity_meta(self, project: OpenProject, graph_updated_at: object) -> None:
        graph_timestamp = str(graph_updated_at or project.metadata.created_at)
        if project.state_db.get_project_meta('last_graph_edit_at') is None:
            project.state_db.set_project_meta('last_graph_edit_at', graph_timestamp)
        if project.state_db.get_project_meta('last_notebook_edit_at') is None:
            project.state_db.set_project_meta('last_notebook_edit_at', project.metadata.created_at)

    def _dashboard_ui_payload(self, dashboard_id: str, *, base_ui: dict[str, Any]) -> dict[str, Any]:
        if self.dashboard_service is None:
            return base_ui
        try:
            dashboard = self.dashboard_service.get_dashboard(dashboard_id)
        except (NotFoundError, FileNotFoundError, ValueError):
            return base_ui
        counts = {'pending': 0, 'stale': 0, 'ready': 0}
        state_db = self.require_project().state_db
        for panel in dashboard.get('panels', []):
            if not isinstance(panel, dict):
                continue
            node_id = str(panel.get('node_id') or '').strip()
            asset_name = str(panel.get('asset_name') or '').strip()
            if not node_id or not asset_name:
                continue
            head = state_db.get_asset_head(node_id, asset_name)
            state = str(head.get('state') or 'pending') if isinstance(head, dict) else 'pending'
            if state in counts:
                counts[state] += 1
        return {
            **base_ui,
            'source_count': len(dashboard.get('sources', [])),
            'panel_count': len(dashboard.get('panels', [])),
            'asset_counts': counts,
        }


def _as_str(value: object) -> str:
    return str(value)


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _notice_sort_key(notice: dict[str, Any]) -> tuple[int, str, str]:
    severity_rank = 0 if notice.get('severity') == ValidationSeverity.ERROR.value else 1
    created_at = str(notice.get('created_at') or '')
    issue_id = str(notice.get('issue_id') or '')
    return (severity_rank, created_at, issue_id)
