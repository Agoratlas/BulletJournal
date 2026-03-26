from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

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
    if stream not in {'stdout', 'stderr'}:
        raise HTTPException(status_code=404, detail='Unknown execution log stream.')
    container = request.app.state.container
    project = container.project_service.require_project()
    execution_meta = project.state_db.list_orchestrator_execution_meta().get(node_id)
    if execution_meta is None:
        raise HTTPException(status_code=404, detail='No execution metadata found for node.')
    run_id = execution_meta.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(status_code=404, detail='No execution log found for node.')
    log_path = project.paths.execution_logs_dir / f'{run_id}_{node_id}.{stream}.log'
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail='No execution log found for node.')
    return FileResponse(Path(log_path), media_type='text/plain; charset=utf-8', filename=log_path.name)
