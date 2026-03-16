from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix='/projects/{project_id}/templates', tags=['templates'])


@router.get('')
def list_templates(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.template_service.list_templates()
