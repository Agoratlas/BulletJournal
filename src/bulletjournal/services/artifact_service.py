from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, NodeKind, StorageKind
from bulletjournal.domain.models import file_input_artifact_name
from bulletjournal.services.graph_service import GraphService
from bulletjournal.utils import utc_now_iso


class ArtifactService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def list_artifacts(self) -> list[dict[str, Any]]:
        return self.project_service.require_project().state_db.list_artifact_heads()

    def get_artifact(self, node_id: str, artifact_name: str) -> dict[str, Any]:
        head = self.project_service.require_project().state_db.get_artifact_head(node_id, artifact_name)
        if head is None:
            raise NotFoundError(f'Unknown artifact `{node_id}/{artifact_name}`.')
        return head

    def upload_file(self, node_id: str, filename: str, content: bytes, mime_type: str | None = None) -> dict[str, Any]:
        project = self.project_service.require_project()
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.FILE_INPUT:
            raise InvalidRequestError(f'Node `{node_id}` is not a file input node.')
        artifact_name = file_input_artifact_name(node)
        temp_path = project.object_store.create_temp_file(Path(filename).suffix)
        try:
            temp_path.write_bytes(content)
            persisted = project.object_store.persist_file(temp_path, extension=Path(filename).suffix)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        project.state_db.upsert_artifact_object(
            persisted['artifact_hash'],
            persisted['storage_kind'],
            persisted['data_type'],
            persisted['size_bytes'],
            persisted.get('extension'),
            mime_type or persisted.get('mime_type') or mimetypes.guess_type(filename)[0],
            {
                **(persisted.get('preview') or {}),
                'original_filename': filename,
                'uploaded_at': utc_now_iso(),
            },
        )
        previous = project.state_db.get_artifact_head(node_id, artifact_name)
        version_id = project.state_db.create_artifact_version(
            node_id=node_id,
            artifact_name=artifact_name,
            role=ArtifactRole.OUTPUT,
            artifact_hash=persisted['artifact_hash'],
            source_hash='file_input',
            upstream_code_hash=persisted['artifact_hash'],
            upstream_data_hash=persisted['artifact_hash'],
            run_id=f'upload:{node_id}:{utc_now_iso()}',
            lineage_mode=LineageMode.MANAGED,
            warnings=[],
            state=ArtifactState.READY,
        )
        old_state = None if previous is None else previous['state']
        self.project_service.event_service.publish(
            'artifact.state_changed',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={
                'node_id': node_id,
                'artifact_name': artifact_name,
                'old_state': old_state,
                'new_state': ArtifactState.READY.value,
                'version_id': version_id,
            },
        )
        if self.project_service.run_service is not None:
            self.project_service.run_service.interrupt_active_run_if_nodes_affected(
                [node_id],
                self.project_service.graph(),
            )
        GraphService(self.project_service).mark_downstream_stale([node_id])
        return self.get_artifact(node_id, artifact_name)

    def download_file(self, node_id: str, artifact_name: str) -> dict[str, Any]:
        head = self.get_artifact(node_id, artifact_name)
        if not head.get('artifact_hash'):
            raise FileNotFoundError(f'Artifact `{node_id}/{artifact_name}` is pending.')
        project = self.project_service.require_project()
        project.state_db.touch_artifact_object(str(head['artifact_hash']))
        filename = self._download_filename(head)
        return {
            'path': project.object_store.load_file_path(str(head['artifact_hash'])),
            'filename': filename,
            'mime_type': self._download_mime_type(head, filename),
        }

    @staticmethod
    def _download_filename(head: dict[str, Any]) -> str:
        stem = ArtifactService._sanitize_filename_stem(str(head.get('artifact_name') or 'artifact'))
        extension = ArtifactService._download_extension(head)
        if extension and stem.lower().endswith(extension.lower()):
            return stem
        return f'{stem}{extension}'

    @staticmethod
    def _sanitize_filename_stem(value: str) -> str:
        candidate = ''.join(char if char.isalnum() or char in {'-', '_', ' '} else '_' for char in value).strip()
        candidate = ' '.join(candidate.split())
        return candidate or 'artifact'

    @staticmethod
    def _download_extension(head: dict[str, Any]) -> str:
        extension = head.get('extension')
        if isinstance(extension, str) and extension:
            return extension if extension.startswith('.') else f'.{extension}'
        mime_type = head.get('mime_type')
        if isinstance(mime_type, str) and mime_type:
            guessed = mimetypes.guess_extension(mime_type, strict=False)
            if guessed:
                return guessed
        storage_kind = head.get('storage_kind')
        if storage_kind == StorageKind.JSON.value:
            return '.json'
        if storage_kind == StorageKind.PARQUET.value:
            return '.parquet'
        if storage_kind == StorageKind.PICKLE.value:
            return '.pkl.gz'
        if storage_kind == StorageKind.FILE.value:
            return '.bin'
        return '.bin'

    @staticmethod
    def _download_mime_type(head: dict[str, Any], filename: str) -> str:
        mime_type = head.get('mime_type')
        if isinstance(mime_type, str) and mime_type:
            return mime_type
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or 'application/octet-stream'
