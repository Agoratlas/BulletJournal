from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.models import GraphData, Node, NotebookInterface, Port, ProjectMetadata, file_input_artifact_name
from bulletjournal.domain.state_machine import derive_node_state
from bulletjournal.execution.watcher import NotebookWatcher
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root, is_project_root, load_project_json
from bulletjournal.storage.state_db import StateDB


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
        self.current_project: OpenProject | None = None
        self.run_service = None
        self.watcher = NotebookWatcher(self)

    def init_project(self, path: Path, *, title: str | None = None) -> dict[str, Any]:
        paths = init_project_root(path, title=title)
        self._open_paths(paths)
        self.reparse_all_notebooks()
        return self.snapshot()

    def open_project(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        if not is_project_root(resolved):
            raise FileNotFoundError(f'{resolved} is not an BulletJournal project root.')
        self._open_paths(ProjectPaths(resolved))
        self.reparse_all_notebooks()
        return self.snapshot()

    def _open_paths(self, paths: ProjectPaths) -> OpenProject:
        project_json = load_project_json(paths)
        metadata = ProjectMetadata(
            project_id=_as_str(project_json['project_id']),
            title=_as_str(project_json['title']),
            created_at=_as_str(project_json['created_at']),
            artifact_cache_limit_bytes=_as_int(project_json['artifact_cache_limit_bytes']) if 'artifact_cache_limit_bytes' in project_json else 0,
            tracked_env_vars=_as_str_list(project_json['tracked_env_vars']) if 'tracked_env_vars' in project_json else [],
            default_open_browser=bool(project_json.get('default_open_browser', True)),
        )
        graph_store = GraphStore(paths)
        state_db = StateDB(paths.state_db_path)
        object_store = ObjectStore(paths)
        project = OpenProject(paths=paths, metadata=metadata, graph_store=graph_store, state_db=state_db, object_store=object_store)
        state_db.abort_inflight_runs()
        self.current_project = project
        self.watcher.start()
        graph = graph_store.read()
        self.event_service.publish(
            'project.opened',
            project_id=metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={'project_id': metadata.project_id, 'root': str(paths.root)},
        )
        return project

    def require_project(self) -> OpenProject:
        if self.current_project is None:
            raise InvalidRequestError('No project is currently open.')
        return self.current_project

    def require_project_id(self, project_id: str) -> OpenProject:
        project = self.require_project()
        if project.metadata.project_id != project_id:
            raise NotFoundError(f'Unknown project `{project_id}`.')
        return project

    def graph(self) -> GraphData:
        return self.require_project().graph_store.read()

    def write_graph(self, graph: GraphData, *, increment_version: bool = True) -> GraphData:
        project = self.require_project()
        graph = project.graph_store.write(graph, increment_version=increment_version)
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
                )
            ],
            assets=[],
            docs='File input node.',
            issues=[],
        )

    def validation_issues(self, *, node_id: str | None = None, include_dismissed: bool = False) -> list[dict[str, Any]]:
        return self.require_project().state_db.list_validation_issues(node_id=node_id, include_dismissed=include_dismissed)

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
            if issue['severity'] != ValidationSeverity.WARNING.value:
                raise InvalidRequestError('Errors cannot be dismissed.')
            project.state_db.dismiss_validation_issue(issue_id)
        else:
            issue = project.state_db.get_persistent_notice(issue_id)
            if issue is None:
                raise NotFoundError(f'Unknown notice `{issue_id}`.')
            if issue['severity'] != ValidationSeverity.WARNING.value:
                raise InvalidRequestError('Errors cannot be dismissed.')
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
        project.state_db.save_persistent_notice(
            issue_id=issue_id,
            node_id=node_id,
            severity=severity,
            code=code,
            message=message,
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
                'message': message,
                'details': resolved_details,
            },
        )
        if notice is None:
            raise RuntimeError(f'Failed to load persisted notice `{issue_id}`.')
        return notice

    def snapshot(self) -> dict[str, Any]:
        project = self.require_project()
        graph = project.graph_store.read()
        interfaces = self.interfaces_by_node()
        validation = self.validation_issues()
        notices = self.notices()
        artifacts = project.state_db.list_artifact_heads()
        artifact_states_by_node: dict[str, list[str]] = {}
        for artifact in artifacts:
            artifact_states_by_node.setdefault(str(artifact['node_id']), []).append(str(artifact['state']))
        runs = project.state_db.list_run_records()
        node_payload = []
        last_run_by_node: dict[str, bool] = {}
        running_nodes: set[str] = set()
        for run in runs:
            raw_target = run.get('target_json')
            target = raw_target if isinstance(raw_target, dict) else {}
            node_id_value = target.get('node_id') if isinstance(target, dict) else None
            if node_id_value is not None:
                node_key = str(node_id_value)
                if node_key not in last_run_by_node:
                    last_run_by_node[node_key] = run['status'] == 'failed'
                if run['status'] in {'queued', 'running'} and run.get('mode') != 'edit_run':
                    running_nodes.add(node_key)
        for node in graph.nodes:
            node_payload.append(
                {
                    **node.to_dict(),
                    'interface': interfaces.get(node.id),
                    'state': derive_node_state(
                        artifact_states_by_node.get(node.id, []),
                        run_failed=last_run_by_node.get(node.id, False),
                        running=node.id in running_nodes,
                    ),
                }
            )
        return {
            'project': {
                'project_id': project.metadata.project_id,
                'title': project.metadata.title,
                'created_at': project.metadata.created_at,
                'root': str(project.paths.root),
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

    def reparse_all_notebooks(self) -> None:
        project = self.require_project()
        graph = project.graph_store.read()
        from bulletjournal.services.notebook_service import NotebookService  # local import to avoid cycle

        notebook_service = NotebookService(self)
        for node in graph.nodes:
            if node.kind == NodeKind.FILE_INPUT:
                project.state_db.ensure_artifact_head(node.id, file_input_artifact_name(node), ArtifactState.PENDING)
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


def _as_str(value: object) -> str:
    return str(value)


def _as_int(value: object) -> int:
    return int(str(value))


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _notice_sort_key(notice: dict[str, Any]) -> tuple[int, str, str]:
    severity_rank = 0 if notice.get('severity') == ValidationSeverity.ERROR.value else 1
    created_at = str(notice.get('created_at') or '')
    issue_id = str(notice.get('issue_id') or '')
    return (severity_rank, created_at, issue_id)
