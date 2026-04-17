from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=['templates'])


@router.get('/templates')
def list_templates(request: Request):
    container = request.app.state.container
    return container.template_service.list_templates()
