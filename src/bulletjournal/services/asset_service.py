from __future__ import annotations

from typing import Any

from bulletjournal.domain.errors import NotFoundError


class AssetService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def list_assets_for_node(self, node_id: str) -> list[dict[str, Any]]:
        self.project_service.get_node(node_id)
        state_db = self.project_service.require_project().state_db
        annotations = state_db.list_asset_annotations(node_id)
        return [self._with_annotation(asset, annotations) for asset in state_db.list_asset_heads(node_id=node_id)]

    def get_asset(self, node_id: str, asset_name: str) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        return self._with_annotation(
            head, self.project_service.require_project().state_db.list_asset_annotations(node_id)
        )

    @staticmethod
    def _with_annotation(asset: dict[str, Any], annotations: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {**asset, 'annotation': annotations.get(asset['asset_name'], {'is_favorite': False, 'note': ''})}

    def patch_annotation(
        self, node_id: str, asset_name: str, *, is_favorite: bool | None, note: str | None
    ) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        project = self.project_service.require_project()
        project.state_db.patch_asset_annotation(node_id, asset_name, is_favorite=is_favorite, note=note)
        self.project_service.event_service.publish(
            'asset.annotation_updated',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={'node_id': node_id, 'asset_name': asset_name},
        )
        if is_favorite is not None:
            self.project_service.event_service.publish(
                'dashboard.updated',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'dashboard_id': '@favorites',
                    'version': project.state_db.get_favorites_dashboard()['version'],
                },
            )
        return self.get_asset(node_id, asset_name)
