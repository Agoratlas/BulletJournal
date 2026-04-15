from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, NodeKind, StorageKind
from bulletjournal.domain.models import file_input_artifact_name
from bulletjournal.services.graph_service import GraphService
from bulletjournal.utils import utc_now_iso

DATAFRAME_CSV_DOWNLOAD_MAX_BYTES = 100_000_000


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
        blockers = self.project_service.frozen_block_blockers_for_stale_roots([node_id])
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
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

    def set_artifact_state(
        self,
        node_id: str,
        artifact_name: str,
        *,
        state: ArtifactState,
        propagate_downstream_stale: bool = True,
    ) -> dict[str, Any]:
        if state == ArtifactState.READY and not self._node_inputs_are_ready(node_id):
            raise InvalidRequestError(
                f'Node `{node_id}` has stale or pending inputs. Its outputs cannot be marked ready.'
            )
        head = self.get_artifact(node_id, artifact_name)
        if head.get('current_version_id') is None:
            raise InvalidRequestError(
                f'Artifact `{node_id}/{artifact_name}` is pending and cannot be marked {state.value}.'
            )
        current_state = str(head['state'])
        if current_state == state.value:
            return head
        project = self.project_service.require_project()
        project.state_db.set_artifact_head_state(node_id, artifact_name, state)
        self.project_service.event_service.publish(
            'artifact.state_changed',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={
                'node_id': node_id,
                'artifact_name': artifact_name,
                'old_state': current_state,
                'new_state': state.value,
            },
        )
        if state == ArtifactState.STALE and propagate_downstream_stale:
            GraphService(self.project_service).mark_downstream_stale([node_id])
        return self.get_artifact(node_id, artifact_name)

    def set_node_output_states(
        self,
        node_id: str,
        *,
        state: ArtifactState,
        only_current_state: ArtifactState | None = None,
    ) -> dict[str, Any]:
        if state == ArtifactState.READY and not self._node_inputs_are_ready(node_id):
            raise InvalidRequestError(
                f'Node `{node_id}` has stale or pending inputs. Its outputs cannot be marked ready.'
            )
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            raise InvalidRequestError(f'Node `{node_id}` does not have a parsed interface yet.')
        changed_artifacts: list[str] = []
        for port in interface.get('outputs', []) + interface.get('assets', []):
            artifact_name = str(port['name'])
            head = self.project_service.require_project().state_db.get_artifact_head(node_id, artifact_name)
            if head is None or head.get('current_version_id') is None:
                continue
            current_state = ArtifactState(str(head['state']))
            if only_current_state is not None and current_state != only_current_state:
                continue
            if current_state == state:
                continue
            self.set_artifact_state(
                node_id,
                artifact_name,
                state=state,
                propagate_downstream_stale=False,
            )
            changed_artifacts.append(artifact_name)
        if changed_artifacts and state == ArtifactState.STALE:
            GraphService(self.project_service).mark_downstream_stale([node_id])
        return {
            'node_id': node_id,
            'artifact_names': changed_artifacts,
            'state': state.value,
            'only_current_state': None if only_current_state is None else only_current_state.value,
        }

    def _node_inputs_are_ready(self, node_id: str) -> bool:
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.NOTEBOOK:
            return True
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return False
        graph = self.project_service.graph()
        state_db = self.project_service.require_project().state_db
        for port in interface.get('inputs', []):
            binding = resolve_input_binding(graph, node_id=node_id, input_name=str(port['name']))
            if binding is None:
                if bool(port.get('has_default', False)):
                    continue
                return False
            head = state_db.get_artifact_head(binding[0], binding[1])
            if head is None or head.get('current_version_id') is None:
                return False
            if head.get('state') != ArtifactState.READY.value:
                return False
        return True

    def download_file(self, node_id: str, artifact_name: str, *, download_format: str | None = None) -> dict[str, Any]:
        head = self.get_artifact(node_id, artifact_name)
        if not head.get('artifact_hash'):
            raise FileNotFoundError(f'Artifact `{node_id}/{artifact_name}` is pending.')
        project = self.project_service.require_project()
        project.state_db.touch_artifact_object(str(head['artifact_hash']))
        if download_format == 'csv':
            return self._download_dataframe_csv(project, head)
        if download_format not in {None, 'parquet'}:
            raise InvalidRequestError(f'Unknown artifact download format `{download_format}`.')
        filename = self._download_filename(head)
        return {
            'kind': 'path',
            'path': project.object_store.load_file_path(str(head['artifact_hash'])),
            'filename': filename,
            'mime_type': self._download_mime_type(head, filename),
        }

    def _download_dataframe_csv(self, project, head: dict[str, Any]) -> dict[str, Any]:
        if head.get('data_type') != 'pandas.DataFrame':
            raise InvalidRequestError('CSV downloads are only available for DataFrame artifacts.')
        size_bytes = int(head.get('size_bytes') or 0)
        if size_bytes > DATAFRAME_CSV_DOWNLOAD_MAX_BYTES:
            raise InvalidRequestError('CSV downloads are limited to DataFrame artifacts no larger than 100 MB.')
        frame = project.object_store.load_value(str(head['artifact_hash']), str(head['data_type']))
        csv_bytes = frame.to_csv(index=False).encode('utf-8')
        return {
            'kind': 'bytes',
            'content': csv_bytes,
            'filename': f'{self._sanitize_filename_stem(str(head.get("artifact_name") or "artifact"))}.csv',
            'mime_type': 'text/csv; charset=utf-8',
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
