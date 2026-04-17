from __future__ import annotations

import shutil
from pathlib import Path

from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.errors import NotFoundError
from bulletjournal.domain.models import file_input_artifact_name
from bulletjournal.utils import copy_tree, utc_now_iso


class CheckpointService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def create_checkpoint(self) -> dict[str, object]:
        project = self.project_service.require_project()
        checkpoint_id = utc_now_iso().replace(':', '-').replace('Z', 'Z')
        checkpoint_dir = project.paths.checkpoints_dir / checkpoint_id
        copy_tree(project.paths.graph_dir, checkpoint_dir / 'graph')
        copy_tree(project.paths.notebooks_dir, checkpoint_dir / 'notebooks')
        graph_version = int(self.project_service.graph().meta['graph_version'])
        project.state_db.create_checkpoint(checkpoint_id, graph_version, str(checkpoint_dir))
        self.project_service.event_service.publish(
            'checkpoint.created',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={'checkpoint_id': checkpoint_id, 'path': str(checkpoint_dir)},
        )
        return {'checkpoint_id': checkpoint_id, 'path': str(checkpoint_dir), 'graph_version': graph_version}

    def list_checkpoints(self) -> list[dict[str, object]]:
        return [
            checkpoint.__dict__ for checkpoint in self.project_service.require_project().state_db.list_checkpoints()
        ]

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, object]:
        project = self.project_service.require_project()
        checkpoints = {checkpoint.checkpoint_id: checkpoint for checkpoint in project.state_db.list_checkpoints()}
        checkpoint = checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise NotFoundError(f'Unknown checkpoint `{checkpoint_id}`.')
        checkpoint_path = Path(checkpoint.path)
        if not checkpoint_path.exists():
            raise FileNotFoundError(f'Checkpoint path missing: {checkpoint_path}')
        if project.paths.graph_dir.exists():
            shutil.rmtree(project.paths.graph_dir)
        if project.paths.notebooks_dir.exists():
            shutil.rmtree(project.paths.notebooks_dir)
        copy_tree(checkpoint_path / 'graph', project.paths.graph_dir)
        copy_tree(checkpoint_path / 'notebooks', project.paths.notebooks_dir)
        self._drop_state_for_missing_nodes()
        graph = self.project_service.graph()
        self.project_service.write_graph(graph)
        self.project_service.reparse_all_notebooks()
        self._reconcile_artifact_state()
        self._mark_restored_notebooks_stale()
        project.state_db.mark_checkpoint_restored(checkpoint_id)
        self.project_service.event_service.publish(
            'checkpoint.restored',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={'checkpoint_id': checkpoint_id},
        )
        return {'checkpoint_id': checkpoint_id, 'status': 'restored'}

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
            if node.kind in {NodeKind.ORGANIZER, NodeKind.AREA}:
                allowed_artifacts[node.id] = set()
                continue
            interface = self.project_service.latest_interface(node.id)
            if interface is None:
                allowed_artifacts[node.id] = set()
                continue
            names = {str(port['name']) for port in interface.get('outputs', []) + interface.get('assets', [])}
            allowed_artifacts[node.id] = names
            for artifact_name in names:
                project.state_db.ensure_artifact_head(node.id, artifact_name, ArtifactState.PENDING)
        for head in project.state_db.list_artifact_heads():
            node_id = str(head['node_id'])
            artifact_name = str(head['artifact_name'])
            if artifact_name not in allowed_artifacts.get(node_id, set()):
                project.state_db.delete_artifact_state(node_id, artifact_name)
