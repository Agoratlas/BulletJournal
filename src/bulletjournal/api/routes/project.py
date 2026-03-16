from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import InitProjectRequest, NoticeDismissResponse, OpenProjectRequest

router = APIRouter(prefix='/projects', tags=['projects'])


@router.post('/open')
def open_project(payload: OpenProjectRequest, request: Request):
    container = request.app.state.container
    return container.project_service.open_project(Path(payload.path))


@router.post('/init')
def init_project(payload: InitProjectRequest, request: Request):
    container = request.app.state.container
    return container.project_service.init_project(Path(payload.path), title=payload.title)


@router.get('/current')
def current_project(request: Request):
    container = request.app.state.container
    return container.project_service.snapshot()


@router.get('/{project_id}/snapshot')
def snapshot(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.project_service.snapshot()


@router.post('/{project_id}/notices/{issue_id}/dismiss', response_model=NoticeDismissResponse)
def dismiss_notice(project_id: str, issue_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.project_service.dismiss_notice(issue_id)
