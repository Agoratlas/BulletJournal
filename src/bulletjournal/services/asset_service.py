from __future__ import annotations

from typing import Any

from bulletjournal.domain.errors import NotFoundError


class AssetService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def list_assets_for_node(self, node_id: str) -> list[dict[str, Any]]:
        self.project_service.get_node(node_id)
        return self.project_service.require_project().state_db.list_asset_heads(node_id=node_id)

    def get_asset(self, node_id: str, asset_name: str) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        return head
