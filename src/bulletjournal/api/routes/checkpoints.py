from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix='/projects/{project_id}/checkpoints', tags=['checkpoints'])


@router.get('')
def list_checkpoints(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.checkpoint_service.list_checkpoints()


@router.post('')
def create_checkpoint(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.checkpoint_service.create_checkpoint()


@router.post('/{checkpoint_id}/restore')
def restore_checkpoint(project_id: str, checkpoint_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.checkpoint_service.restore_checkpoint(checkpoint_id)
