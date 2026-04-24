from __future__ import annotations

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import RunAllRequest, RunNodeRequest, RunSelectionRequest

router = APIRouter(tags=['runs'])


@router.post('/nodes/{node_id}/run')
def run_node(node_id: str, payload: RunNodeRequest, request: Request):
    container = request.app.state.container
    mode = payload.mode.value
    action = None if payload.action is None else payload.action.value
    scope = payload.scope.value
    result = container.run_service.start_node_run(node_id, mode=mode, action=action, scope=scope)
    if isinstance(result, dict) and isinstance(result.get('url'), str) and str(result['url']).startswith('/'):
        result = dict(result)
        result['url'] = str(request.base_url).rstrip('/') + str(result['url'])
    return result


@router.post('/runs/run-all')
def run_all(payload: RunAllRequest, request: Request):
    container = request.app.state.container
    _ = payload
    return container.run_service.run_all_stale()


@router.post('/runs/run-selection')
def run_selection(payload: RunSelectionRequest, request: Request):
    container = request.app.state.container
    _ = payload.mode
    action = None if payload.action is None else payload.action.value
    return container.run_service.start_selection_run(payload.node_ids, action=action)


@router.post('/runs/{run_id}/cancel')
def cancel_run(run_id: str, request: Request):
    container = request.app.state.container
    return container.run_service.cancel_run(run_id)


@router.get('/sessions')
def list_sessions(request: Request):
    container = request.app.state.container
    sessions = []
    for session in container.run_service.list_sessions():
        resolved = dict(session)
        if isinstance(resolved.get('url'), str) and str(resolved['url']).startswith('/'):
            resolved['url'] = str(request.base_url).rstrip('/') + str(resolved['url'])
        sessions.append(resolved)
    return sessions


@router.post('/sessions/{session_id}/stop')
def stop_session(session_id: str, request: Request):
    container = request.app.state.container
    return container.run_service.stop_session(session_id)
