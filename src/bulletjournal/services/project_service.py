from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.graph_bindings import organizer_interface_for_node
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
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root, load_project_json, require_project_root
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
        self.project: OpenProject | None = None
        self.run_service = None
        self.watcher = NotebookWatcher(self)

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
        object_store = ObjectStore(paths)
        project = OpenProject(
            paths=paths, metadata=metadata, graph_store=graph_store, state_db=state_db, object_store=object_store
        )
        state_db.abort_inflight_runs()
        self.project = project
        self.watcher.start()
        graph = graph_store.read()
        self._ensure_activity_meta(project, graph.meta.get('updated_at'))
        self.event_service.publish(
            'project.opened',
            project_id=metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={'project_id': metadata.project_id, 'root': str(paths.root)},
        )
        return project

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
            assets=[],
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
            assets=[],
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
        artifacts = project.state_db.list_artifact_heads()
        artifact_states_by_node: dict[str, list[str]] = {}
        for artifact in artifacts:
            artifact_states_by_node.setdefault(str(artifact['node_id']), []).append(str(artifact['state']))
        runs = project.state_db.list_run_records()
        execution_meta_by_node = project.state_db.list_orchestrator_execution_meta()
        orchestrator_state_by_node = self.run_service.orchestrator_state() if self.run_service is not None else {}
        node_payload = []
        last_run_by_node: dict[str, bool] = {}
        for run in runs:
            raw_target = run.get('target_json')
            target = raw_target if isinstance(raw_target, dict) else {}
            target_node_ids: list[str] = []
            failure = run.get('failure_json') if isinstance(run.get('failure_json'), dict) else None
            failed_node_id = (
                str(failure.get('node_id')).strip()
                if isinstance(failure, dict) and failure.get('node_id') is not None
                else ''
            )
            if run['status'] == 'failed' and failed_node_id:
                target_node_ids.append(failed_node_id)
            if isinstance(target, dict):
                if not target_node_ids:
                    node_id_value = target.get('node_id')
                    if node_id_value is not None:
                        target_node_ids.append(str(node_id_value))
                if not target_node_ids:
                    raw_node_ids = target.get('node_ids')
                    if isinstance(raw_node_ids, list):
                        target_node_ids.extend(str(node_id) for node_id in raw_node_ids)
                if not target_node_ids:
                    raw_plan = target.get('plan')
                    if isinstance(raw_plan, list):
                        target_node_ids.extend(str(node_id) for node_id in raw_plan)
            deduped_target_node_ids = list(dict.fromkeys(target_node_ids))
            for node_key in deduped_target_node_ids:
                if node_key not in last_run_by_node:
                    last_run_by_node[node_key] = run['status'] == 'failed'
        for node in graph.nodes:
            interface = interfaces.get(node.id)
            template_status = None
            resolved_template = node.template
            if node.template is not None:
                try:
                    resolved_template = self.template_service.template_ref(node.template.ref)
                except FileNotFoundError:
                    resolved_template = node.template
            if resolved_template and interface is not None and node.kind == NodeKind.NOTEBOOK:
                template_source = self.template_service.resolve_template_source(resolved_template.ref)
                template_status = (
                    'template' if interface.get('source_hash') == template_source.source_hash else 'modified'
                )
            orchestrator_state = orchestrator_state_by_node.get(node.id)
            node_payload.append(
                {
                    **node.to_dict(),
                    'template': resolved_template.to_dict() if resolved_template else None,
                    'interface': interface,
                    'template_status': template_status,
                    'execution_meta': execution_meta_by_node.get(node.id),
                    'orchestrator_state': orchestrator_state,
                    'state': derive_node_state(
                        artifact_states_by_node.get(node.id, []),
                        run_failed=last_run_by_node.get(node.id, False),
                        running=orchestrator_state is not None and orchestrator_state.get('status') == 'running',
                        queued=orchestrator_state is not None and orchestrator_state.get('status') == 'queued',
                        validation_failed=validation_errors_by_node.get(node.id, False),
                    ),
                }
            )
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

    def record_notebook_activity(self, timestamp: str | None = None) -> None:
        self.require_project().state_db.set_project_meta('last_notebook_edit_at', timestamp or utc_now_iso())

    def mark_environment_changed(self, *, reason: str, mark_all_artifacts_stale: bool = True) -> dict[str, Any]:
        project = self.require_project()
        stale_count = 0
        if mark_all_artifacts_stale:
            notebook_ids = {node.id for node in self.graph().nodes if node.kind == NodeKind.NOTEBOOK}
            for head in project.state_db.list_artifact_heads():
                if head['node_id'] not in notebook_ids:
                    continue
                if head['current_version_id'] is None or head['state'] == ArtifactState.STALE.value:
                    continue
                project.state_db.set_artifact_head_state(head['node_id'], head['artifact_name'], ArtifactState.STALE)
                stale_count += 1
            graph_version = int(self.graph().meta['graph_version'])
            self.event_service.publish(
                'project.environment_changed',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'reason': reason, 'mark_all_artifacts_stale': True, 'stale_count': stale_count},
            )
        notice = self.record_notice(
            issue_id='environment_changed',
            node_id=None,
            severity=ValidationSeverity.WARNING,
            code='environment_changed',
            message='Project outputs were marked stale because the environment changed.',
            details={'reason': reason, 'mark_all_artifacts_stale': mark_all_artifacts_stale},
        )
        return {
            'project_id': project.metadata.project_id,
            'reason': reason,
            'mark_all_artifacts_stale': mark_all_artifacts_stale,
            'stale_count': stale_count,
            'notice': notice,
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
            if node.kind in {NodeKind.ORGANIZER, NodeKind.AREA}:
                continue
            notebook_service.reparse_notebook(node.id)

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
