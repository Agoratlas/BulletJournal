from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=['assets'])


@router.get('/nodes/{node_id}/assets')
def list_node_assets(node_id: str, request: Request):
    return request.app.state.container.asset_service.list_assets_for_node(node_id)


@router.get('/nodes/{node_id}/assets/{asset_name}')
def get_node_asset(node_id: str, asset_name: str, request: Request):
    return request.app.state.container.asset_service.get_asset(node_id, asset_name)
