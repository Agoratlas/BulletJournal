from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix='/checkpoints', tags=['checkpoints'])


@router.get('')
def list_checkpoints(request: Request):
    container = request.app.state.container
    return container.checkpoint_service.list_checkpoints()


@router.post('')
def create_checkpoint(request: Request):
    container = request.app.state.container
    return container.checkpoint_service.create_checkpoint()


@router.post('/{checkpoint_id}/restore')
def restore_checkpoint(checkpoint_id: str, request: Request):
    container = request.app.state.container
    return container.checkpoint_service.restore_checkpoint(checkpoint_id)
