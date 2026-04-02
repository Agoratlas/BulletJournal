from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from bulletjournal.api.schemas import GraphPatchRequest

router = APIRouter(tags=['graph'])


@router.get('/graph')
def get_graph(request: Request):
    container = request.app.state.container
    return container.graph_service.get_graph()


@router.patch('/graph')
def patch_graph(payload: GraphPatchRequest, request: Request):
    container = request.app.state.container
    operations = [operation.model_dump(mode='python') for operation in payload.operations]
    return container.graph_service.apply_operations(payload.graph_version, operations)


@router.get('/nodes/{node_id}')
def get_node(node_id: str, request: Request):
    container = request.app.state.container
    node = container.project_service.get_node(node_id)
    return {
        **node.to_dict(),
        'interface': container.project_service.latest_interface(node_id),
    }


@router.get('/nodes/{node_id}/notebook/download')
def download_notebook(node_id: str, request: Request):
    container = request.app.state.container
    node = container.project_service.get_node(node_id)
    if node.kind != 'notebook':
        raise FileNotFoundError(f'Node `{node_id}` does not have a notebook file.')
    notebook_path = container.project_service.notebook_path(node_id)
    filename = Path(notebook_path).name
    return FileResponse(
        notebook_path,
        media_type='text/x-python; charset=utf-8',
        filename=filename,
    )
