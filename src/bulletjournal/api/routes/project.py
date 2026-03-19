from __future__ import annotations

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import ControllerEnvironmentChangeRequest, NoticeDismissResponse
from bulletjournal.domain.errors import UnauthorizedError

router = APIRouter(tags=['project'])


@router.get('/project')
def get_project(request: Request):
    container = request.app.state.container
    return container.project_service.project_metadata_payload()


@router.get('/project/snapshot')
def snapshot(request: Request):
    container = request.app.state.container
    return container.project_service.snapshot()


@router.get('/project/status')
def project_status(request: Request):
    container = request.app.state.container
    return container.project_service.project_status()


@router.post('/notices/{issue_id}/dismiss', response_model=NoticeDismissResponse)
def dismiss_notice(issue_id: str, request: Request):
    container = request.app.state.container
    return container.project_service.dismiss_notice(issue_id)


@router.get('/controller/status')
def controller_status(request: Request):
    _require_controller_auth(request)
    container = request.app.state.container
    return container.project_service.project_status()


@router.post('/controller/mark-environment-changed')
def mark_environment_changed(payload: ControllerEnvironmentChangeRequest, request: Request):
    _require_controller_auth(request)
    container = request.app.state.container
    return container.project_service.mark_environment_changed(
        reason=payload.reason,
        mark_all_artifacts_stale=payload.mark_all_artifacts_stale,
    )


def _require_controller_auth(request: Request) -> None:
    token = request.app.state.server_config.controller_token
    if not token:
        return
    header = request.headers.get('authorization', '')
    if header != f'Bearer {token}':
        raise UnauthorizedError('Missing or invalid controller bearer token.')
