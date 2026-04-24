from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from bulletjournal.api.schemas import (
    ArtifactStateChangeRequest,
    NodeOutputsStateChangeRequest,
)

router = APIRouter(tags=['artifacts'])


@router.get('/artifacts')
def list_artifacts(request: Request):
    container = request.app.state.container
    return container.artifact_service.list_artifacts()


@router.get('/artifacts/{node_id}/{artifact_name}')
def get_artifact(node_id: str, artifact_name: str, request: Request):
    container = request.app.state.container
    return container.artifact_service.get_artifact(node_id, artifact_name)


@router.get('/artifacts/{node_id}/{artifact_name}/download')
def download_artifact(node_id: str, artifact_name: str, request: Request, format: str | None = None):
    container = request.app.state.container
    file_info = container.artifact_service.download_file(node_id, artifact_name, download_format=format)
    if file_info['kind'] == 'bytes':
        return Response(
            content=file_info['content'],
            media_type=file_info['mime_type'],
            headers={'Content-Disposition': f'attachment; filename="{file_info["filename"]}"'},
        )
    return FileResponse(file_info['path'], media_type=file_info['mime_type'], filename=file_info['filename'])


@router.get('/artifacts/{node_id}/{artifact_name}/content')
def artifact_content(node_id: str, artifact_name: str, request: Request):
    container = request.app.state.container
    file_info = container.artifact_service.download_file(node_id, artifact_name)
    return FileResponse(file_info['path'], media_type=file_info['mime_type'])


@router.post('/file-inputs/{node_id}/upload')
async def upload_file(node_id: str, request: Request):
    container = request.app.state.container
    content = await request.body()
    filename = request.headers.get('x-filename', 'upload.bin')
    mime_type = request.headers.get('content-type')
    result = container.artifact_service.upload_file(node_id, filename, content, mime_type)
    return {
        'node_id': node_id,
        'artifact_name': 'file',
        'state': result['state'],
        'artifact_hash': result['artifact_hash'],
    }


@router.get('/nodes/{node_id}/execution-logs/{stream}/download')
def download_execution_log(node_id: str, stream: str, request: Request):
    log_path = _resolve_execution_log_path(node_id=node_id, stream=stream, request=request)
    return FileResponse(Path(log_path), media_type='text/plain; charset=utf-8', filename=log_path.name)


@router.get('/nodes/{node_id}/execution-logs/{stream}')
def get_execution_log(node_id: str, stream: str, request: Request):
    log_path = _resolve_execution_log_path(node_id=node_id, stream=stream, request=request)
    summary = request.app.state.container.project_service.require_project().state_db.list_orchestrator_execution_meta()[
        node_id
    ][stream]
    if summary is None:
        raise HTTPException(status_code=404, detail=f'No `{stream}` execution log found for node `{node_id}`.')
    return {
        'node_id': node_id,
        'stream': stream,
        **summary,
        'download_url': f'/api/v1/nodes/{node_id}/execution-logs/{stream}/download',
        'filename': log_path.name,
    }


@router.get('/nodes/{node_id}/execution-logs')
def get_execution_logs(node_id: str, request: Request):
    container = request.app.state.container
    project = container.project_service.require_project()
    execution_meta = project.state_db.list_orchestrator_execution_meta().get(node_id)
    if execution_meta is None:
        raise HTTPException(status_code=404, detail=f'No execution metadata found for node `{node_id}`.')
    return {
        'node_id': node_id,
        'stdout': execution_meta.get('stdout'),
        'stderr': execution_meta.get('stderr'),
    }


@router.post('/artifacts/{node_id}/{artifact_name}/state')
def set_artifact_state(
    node_id: str,
    artifact_name: str,
    payload: ArtifactStateChangeRequest,
    request: Request,
):
    container = request.app.state.container
    return container.artifact_service.set_artifact_state(
        node_id,
        artifact_name,
        state=payload.state,
    )


@router.post('/nodes/{node_id}/outputs/state')
def set_node_output_states(
    node_id: str,
    payload: NodeOutputsStateChangeRequest,
    request: Request,
):
    container = request.app.state.container
    return container.artifact_service.set_node_output_states(
        node_id,
        state=payload.state,
        only_current_state=payload.only_current_state,
    )


def _resolve_execution_log_path(*, node_id: str, stream: str, request: Request) -> Path:
    if stream not in {'stdout', 'stderr'}:
        raise HTTPException(
            status_code=404,
            detail=f'Unknown execution log stream `{stream}`. Expected `stdout` or `stderr`.',
        )
    container = request.app.state.container
    project = container.project_service.require_project()
    execution_meta = project.state_db.list_orchestrator_execution_meta().get(node_id)
    if execution_meta is None:
        raise HTTPException(status_code=404, detail=f'No execution metadata found for node `{node_id}`.')
    run_id = execution_meta.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(status_code=404, detail=f'No `{stream}` execution log found for node `{node_id}`.')
    log_path = project.paths.execution_logs_dir / f'{run_id}_{node_id}.{stream}.log'
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail=f'No `{stream}` execution log found for node `{node_id}`.')
    return log_path
