from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bulletjournal.api.schemas import DashboardCreateRequest, DashboardPatchRequest, SaveNotebookDashboardRequest
from bulletjournal.services.dashboard_service import DashboardVersionConflictError

router = APIRouter(tags=['dashboards'])


@router.get('/dashboards/{dashboard_id}')
def get_dashboard(dashboard_id: str, request: Request):
    return request.app.state.container.dashboard_service.get_dashboard(dashboard_id)


@router.post('/dashboards')
def create_dashboard(payload: DashboardCreateRequest, request: Request):
    return request.app.state.container.dashboard_service.create_dashboard(
        dashboard_id=payload.dashboard_id,
        title=payload.title,
        sources=[source.model_dump(mode='python') for source in payload.sources],
        panels=[panel.model_dump(mode='python') for panel in payload.panels],
        x=payload.x,
        y=payload.y,
    )


@router.patch('/dashboards/{dashboard_id}')
def patch_dashboard(dashboard_id: str, payload: DashboardPatchRequest, request: Request):
    try:
        return request.app.state.container.dashboard_service.patch_dashboard(
            dashboard_id,
            dashboard_version=payload.dashboard_version,
            title=payload.title,
            sources=None
            if payload.sources is None
            else [source.model_dump(mode='python') for source in payload.sources],
            panels=None if payload.panels is None else [panel.model_dump(mode='python') for panel in payload.panels],
        )
    except DashboardVersionConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={'detail': str(exc), 'dashboard': exc.latest_dashboard},
        )


@router.delete('/dashboards/{dashboard_id}')
def delete_dashboard(dashboard_id: str, request: Request):
    return request.app.state.container.dashboard_service.delete_dashboard(dashboard_id)


@router.post('/nodes/{node_id}/dashboards')
def save_notebook_dashboard(node_id: str, payload: SaveNotebookDashboardRequest, request: Request):
    return request.app.state.container.dashboard_service.save_notebook_dashboard(
        node_id,
        dashboard_id=payload.dashboard_id,
        title=payload.title,
        panels=None if payload.panels is None else [panel.model_dump(mode='python') for panel in payload.panels],
    )
