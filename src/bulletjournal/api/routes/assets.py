from __future__ import annotations

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import AssetPrepareRequest

router = APIRouter(tags=['assets'])


@router.get('/nodes/{node_id}/assets')
def list_node_assets(node_id: str, request: Request):
    return request.app.state.container.asset_service.list_assets_for_node(node_id)


@router.get('/nodes/{node_id}/assets/{asset_name}')
def get_node_asset(node_id: str, asset_name: str, request: Request):
    return request.app.state.container.asset_service.get_asset(node_id, asset_name)


@router.get('/assets/{node_id}/{asset_name}')
def get_asset(node_id: str, asset_name: str, request: Request):
    return request.app.state.container.asset_service.get_asset(node_id, asset_name)


@router.post('/assets/{node_id}/{asset_name}/prepare')
def prepare_asset(node_id: str, asset_name: str, payload: AssetPrepareRequest, request: Request):
    return request.app.state.container.asset_prepare_service.prepare_asset(
        node_id,
        asset_name,
        asset_version_id=payload.asset_version_id,
        modifier_overrides=payload.modifier_overrides,
        transient_modifiers=payload.transient_modifiers,
        panel_context=payload.panel_context,
    )
