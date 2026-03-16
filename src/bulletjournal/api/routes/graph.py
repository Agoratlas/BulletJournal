from __future__ import annotations

from fastapi import APIRouter, Request

from bulletjournal.api.schemas import GraphPatchRequest

router = APIRouter(prefix='/projects/{project_id}', tags=['graph'])


@router.get('/graph')
def get_graph(project_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    return container.graph_service.get_graph()


@router.patch('/graph')
def patch_graph(project_id: str, payload: GraphPatchRequest, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    operations = [operation.model_dump(mode='python') for operation in payload.operations]
    return container.graph_service.apply_operations(payload.graph_version, operations)


@router.get('/graph/nodes/{node_id}')
@router.get('/nodes/{node_id}')
def get_node(project_id: str, node_id: str, request: Request):
    container = request.app.state.container
    container.project_service.require_project_id(project_id)
    node = container.project_service.get_node(node_id)
    return {
        **node.to_dict(),
        'interface': container.project_service.latest_interface(node_id),
    }
