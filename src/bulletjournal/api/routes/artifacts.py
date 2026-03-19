from __future__ import annotations

from fastapi import APIRouter, Request
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
