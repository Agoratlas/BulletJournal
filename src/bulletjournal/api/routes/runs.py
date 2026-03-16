from __future__ import annotations

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import RunAllRequest, RunNodeRequest

router = APIRouter(prefix='/projects/{project_id}', tags=['runs'])


@router.post('/nodes/{node_id}/run')
def run_node(project_id: str, node_id: str, payload: RunNodeRequest, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    mode = payload.mode.value
    action = None if payload.action is None else payload.action.value
    return container.run_service.start_node_run(node_id, mode=mode, action=action)


@router.post('/runs/run-all')
def run_all(project_id: str, payload: RunAllRequest, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    _ = payload
    return container.run_service.run_all_stale()


@router.post('/runs/{run_id}/cancel')
def cancel_run(project_id: str, run_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.run_service.cancel_run(run_id)


@router.get('/sessions')
def list_sessions(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.run_service.list_sessions()
