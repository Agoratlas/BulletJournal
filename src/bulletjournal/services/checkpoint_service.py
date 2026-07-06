from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.errors import NotFoundError
from bulletjournal.domain.models import file_input_artifact_name
from bulletjournal.utils import copy_tree, utc_now_iso

AUTO_CHECKPOINT_INTERVAL = timedelta(minutes=10)


class CheckpointService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def create_checkpoint(self) -> dict[str, object]:
        project = self.project_service.require_project()
        checkpoint_id = self._next_checkpoint_id()
        checkpoint_dir = project.paths.checkpoints_dir / checkpoint_id
        copy_tree(project.paths.graph_dir, checkpoint_dir / 'graph')
        copy_tree(project.paths.notebooks_dir, checkpoint_dir / 'notebooks')
        copy_tree(project.paths.dashboards_dir, checkpoint_dir / 'dashboards')
        graph_version = int(self.project_service.graph().meta['graph_version'])
        project.state_db.create_checkpoint(checkpoint_id, graph_version, str(checkpoint_dir))
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
        with self.project_service.suspend_automatic_checkpoints():
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
