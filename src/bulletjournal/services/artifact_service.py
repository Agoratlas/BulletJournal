from __future__ import annotations

import io
import json
import mimetypes
import uuid
from csv import reader
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, NodeKind, StorageKind
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError
from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.models import constant_artifact_name, constant_data_type, file_input_artifact_name
from bulletjournal.services.graph_service import GraphService
from bulletjournal.storage.project_lock import ProjectLock
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

    def get_constant_value(self, node_id: str) -> Any:
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.CONSTANT:
            raise InvalidRequestError(f'Node `{node_id}` is not a constant block.')
        data_type = constant_data_type(node)
        if data_type in {'file', 'pandas.DataFrame'}:
            raise InvalidRequestError(f'Constant block `{node_id}` does not have a JSON-copyable value.')
        head = self.get_artifact(node_id, constant_artifact_name(node))
        artifact_hash = head.get('artifact_hash')
        if artifact_hash is None:
            raise InvalidRequestError(f'Constant block `{node_id}` has no value.')
        return self.project_service.require_project().object_store.load_value(str(artifact_hash), data_type)

    def upload_file(
        self,
        node_id: str,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        *,
        dataframe_format: str = 'csv_comma',
    ) -> dict[str, Any]:
        node = self.project_service.get_node(node_id)
        blockers = self.project_service.frozen_block_blockers_for_stale_roots([node_id])
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
        if node.kind == NodeKind.FILE_INPUT:
            artifact_name = file_input_artifact_name(node)
            persisted = self._persist_uploaded_file(filename=filename, content=content)
            return self._save_managed_artifact(
                node_id=node_id,
                artifact_name=artifact_name,
                persisted=persisted,
                source_hash='file_input',
                mime_type=mime_type,
                original_filename=filename,
            )
        if node.kind != NodeKind.CONSTANT:
            raise InvalidRequestError(f'Node `{node_id}` does not support file uploads.')
        data_type = constant_data_type(node)
        if data_type == 'file':
            persisted = self._persist_uploaded_file(filename=filename, content=content)
        elif data_type == 'pandas.DataFrame':
            persisted = self._persist_uploaded_dataframe(
                filename=filename,
                content=content,
                dataframe_format=dataframe_format,
            )
        else:
            raise InvalidRequestError(
                f'Constant block `{node_id}` expects `{data_type}` and cannot accept uploaded files.'
            )
        upload_warning = persisted.pop('upload_warning', None)
        result = self._save_managed_artifact(
            node_id=node_id,
            artifact_name=constant_artifact_name(node),
            persisted=persisted,
            source_hash=f'constant:{data_type}',
            mime_type=mime_type,
            original_filename=filename,
        )
        self.project_service.dismiss_undefined_constant_notice(node_id)
        if upload_warning:
            result['upload_warning'] = upload_warning
        return result

    def set_constant_value(
        self,
        node_id: str,
        value: Any,
        *,
        value_json: str | None = None,
        propagate_downstream_stale: bool = True,
        interrupt_active_run: bool = True,
    ) -> dict[str, Any]:
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.CONSTANT:
            raise InvalidRequestError(f'Node `{node_id}` is not a constant block.')
        blockers = self.project_service.frozen_block_blockers_for_stale_roots([node_id])
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
        data_type = constant_data_type(node)
        if data_type in {'file', 'pandas.DataFrame'}:
            raise InvalidRequestError(
                f'Constant block `{node_id}` expects `{data_type}` and must be populated from an uploaded file.'
            )
        value = _resolve_constant_value(data_type=data_type, value=value, value_json=value_json)
        if value is None:
            raise InvalidRequestError(
                'Constant blocks cannot have a null value. Clear the value to leave the block unset.'
            )
        if data_type == 'pandas.Series' and isinstance(value, list):
            value = pd.Series(value)
        try:
            persisted = self.project_service.require_project().object_store.persist_value(value, data_type)
        except TypeError as exc:
            raise InvalidRequestError(str(exc)) from exc
        result = self._save_managed_artifact(
            node_id=node_id,
            artifact_name=constant_artifact_name(node),
            persisted=persisted,
            source_hash=f'constant:{data_type}',
            propagate_downstream_stale=propagate_downstream_stale,
            interrupt_active_run=interrupt_active_run,
        )
        self.project_service.dismiss_undefined_constant_notice(node_id)
        return result

    def clear_constant_value(
        self,
        node_id: str,
        *,
        propagate_downstream_stale: bool = True,
        interrupt_active_run: bool = True,
    ) -> dict[str, Any]:
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.CONSTANT:
            raise InvalidRequestError(f'Node `{node_id}` is not a constant block.')
        blockers = self.project_service.frozen_block_blockers_for_stale_roots([node_id])
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
        artifact_name = constant_artifact_name(node)
        project = self.project_service.require_project()
        incarnation = project.state_db.live_incarnation(node_id)
        if incarnation is None:
            raise InvalidRequestError(f'Node `{node_id}` has no live incarnation.')
        project.state_db.advance_node_incarnation(str(incarnation['incarnation_id']))
        project.state_db.delete_artifact_state(node_id, artifact_name)
        project.state_db.ensure_artifact_head(node_id, artifact_name, ArtifactState.PENDING)
        self.project_service.record_undefined_constant_notice(node)
        if interrupt_active_run and self.project_service.run_service is not None:
            self.project_service.run_service.interrupt_active_run_if_nodes_affected(
                [node_id],
                self.project_service.graph(),
            )
        if propagate_downstream_stale:
            GraphService(self.project_service).mark_downstream_stale([node_id])
        return self.get_artifact(node_id, artifact_name)

    def _persist_uploaded_file(self, *, filename: str, content: bytes) -> dict[str, Any]:
        project = self.project_service.require_project()
        temp_path = project.object_store.create_temp_file(Path(filename).suffix)
        try:
            temp_path.write_bytes(content)
            return project.object_store.persist_file(temp_path, extension=Path(filename).suffix)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _persist_uploaded_dataframe(
        self, *, filename: str, content: bytes, dataframe_format: str = 'csv_comma'
    ) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        separators = {
            'csv_comma': ',',
            'csv_semicolon': ';',
            'csv_tab': '\t',
        }
        try:
            if dataframe_format == 'parquet':
                if suffix != '.parquet':
                    raise InvalidRequestError('Parquet DataFrame uploads must use a `.parquet` file.')
                frame = pd.read_parquet(io.BytesIO(content))
            elif dataframe_format == 'xlsx':
                if suffix != '.xlsx':
                    raise InvalidRequestError('XLSX DataFrame uploads must use a `.xlsx` file.')
                workbook = pd.ExcelFile(io.BytesIO(content))
                if len(workbook.sheet_names) > 1:
                    # The first worksheet is the only one that can become a DataFrame.
                    first_sheet = workbook.sheet_names[0]
                else:
                    first_sheet = 0
                raw_rows = _xlsx_rows(content, first_sheet)
                header, rows = _validated_tabular_rows(raw_rows)
                frame = pd.DataFrame(rows, columns=header)
            else:
                if suffix != '.csv':
                    raise InvalidRequestError('CSV DataFrame uploads must use a `.csv` file.')
                separator = separators.get(dataframe_format)
                if separator is None:
                    raise InvalidRequestError(
                        'DataFrame upload format must be `parquet`, `csv_comma`, `csv_semicolon`, `csv_tab`, or `xlsx`.'
                    )
                raw_rows = list(reader(io.StringIO(content.decode('utf-8-sig')), delimiter=separator))
                header, rows = _validated_tabular_rows(raw_rows)
                frame = pd.DataFrame(rows, columns=header)
        except InvalidRequestError:
            raise
        except Exception as exc:  # pragma: no cover - pandas error surface varies by version
            format_label = {'parquet': 'Parquet', 'xlsx': 'XLSX'}.get(dataframe_format, 'CSV')
            raise InvalidRequestError(f'Failed to parse {format_label} upload for constant block: {exc}.') from exc
        try:
            persisted = self.project_service.require_project().object_store.persist_value(frame, 'pandas.DataFrame')
            if dataframe_format == 'xlsx' and len(workbook.sheet_names) > 1:
                persisted['upload_warning'] = (
                    f'Only the first worksheet (`{workbook.sheet_names[0]}`) was imported; '
                    f'{len(workbook.sheet_names) - 1} additional worksheet(s) were ignored.'
                )
            return persisted
        except Exception as exc:  # pragma: no cover - parquet backend error surface varies by version
            format_label = {'parquet': 'Parquet', 'xlsx': 'XLSX'}.get(dataframe_format, 'CSV')
            raise InvalidRequestError(f'Failed to store {format_label} upload for constant block: {exc}.') from exc

    def _save_managed_artifact(
        self,
        *,
        node_id: str,
        artifact_name: str,
        persisted: dict[str, Any],
        source_hash: str,
        mime_type: str | None = None,
        original_filename: str | None = None,
        propagate_downstream_stale: bool = True,
        interrupt_active_run: bool = True,
    ) -> dict[str, Any]:
        project = self.project_service.require_project()
        incarnation = project.state_db.live_incarnation(node_id)
        if incarnation is None:
            raise InvalidRequestError(f'Node `{node_id}` has no live incarnation.')
        project.state_db.advance_node_incarnation(str(incarnation['incarnation_id']))
        run_id = f'upload:{node_id}:{utc_now_iso()}'
        publication = project.state_db.begin_publication(
            run_id=run_id,
            node_id=node_id,
            source_hash=source_hash,
            graph_version=int(self.project_service.graph().meta['graph_version']),
        )
        project.state_db.upsert_artifact_object(
            persisted['artifact_hash'],
            persisted['storage_kind'],
            persisted['data_type'],
            persisted['size_bytes'],
            persisted.get('extension'),
            mime_type or persisted.get('mime_type') or mimetypes.guess_type(original_filename or artifact_name)[0],
            {
                **(persisted.get('preview') or {}),
                **({'original_filename': original_filename} if original_filename else {}),
                'uploaded_at': utc_now_iso(),
            },
        )
        previous = project.state_db.get_artifact_head(node_id, artifact_name)
        version_id = project.state_db.create_artifact_version(
            node_id=node_id,
            artifact_name=artifact_name,
            role=ArtifactRole.OUTPUT,
            artifact_hash=persisted['artifact_hash'],
            source_hash=source_hash,
            upstream_code_hash=persisted['artifact_hash'],
            upstream_data_hash=persisted['artifact_hash'],
            run_id=run_id,
            lineage_mode=LineageMode.MANAGED,
            warnings=[],
            state=ArtifactState.READY,
            publication_id=str(publication['publication_id']),
        )
        graph = self.project_service.graph()
        from bulletjournal.execution.planner import downstream_closure

        with ProjectLock(project.paths.project_lock_path).exclusive():
            committed = project.state_db.commit_publication(
                str(publication['publication_id']),
                current_source_hash=source_hash,
                downstream_node_ids=downstream_closure(graph, node_id),
            )
        if not committed:
            raise InvalidRequestError('The upload was superseded by a newer block generation.')
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
        if interrupt_active_run and self.project_service.run_service is not None:
            self.project_service.run_service.interrupt_active_run_if_nodes_affected(
                [node_id],
                self.project_service.graph(),
            )
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
        project = self.project_service.require_project()
        changed_artifacts: list[str] = []
        for port in interface.get('outputs', []):
            artifact_name = str(port['name'])
            head = project.state_db.get_artifact_head(node_id, artifact_name)
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
        changed_assets: list[str] = []
        for head in project.state_db.list_asset_heads(node_id=node_id):
            if head.get('current_asset_version_id') is None:
                continue
            current_state = ArtifactState(str(head['state']))
            if only_current_state is not None and current_state != only_current_state:
                continue
            if current_state == state:
                continue
            asset_name = str(head['asset_name'])
            project.state_db.set_asset_head_state(node_id, asset_name, state)
            self.project_service.event_service.publish(
                'asset.state_changed',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'node_id': node_id,
                    'asset_name': asset_name,
                    'old_state': current_state.value,
                    'new_state': state.value,
                },
            )
            changed_assets.append(asset_name)
        if (changed_artifacts or changed_assets) and state == ArtifactState.STALE:
            GraphService(self.project_service).mark_downstream_stale([node_id])
        return {
            'node_id': node_id,
            'artifact_names': changed_artifacts,
            'asset_names': changed_assets,
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
        if download_format == 'xlsx':
            return self._download_dataframe_xlsx(project, head)
        if download_format not in {None, 'parquet'}:
            raise InvalidRequestError(f'Unknown artifact download format `{download_format}`.')
        filename = self._download_filename(head)
        lease_id = project.state_db.acquire_object_lease(
            str(head['artifact_hash']),
            'download',
            str(uuid.uuid4()),
            expires_at=(datetime.now(tz=UTC) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
        )
        return {
            'kind': 'path',
            'path': project.object_store.load_file_path(str(head['artifact_hash'])),
            'filename': filename,
            'mime_type': self._download_mime_type(head, filename),
            'lease_id': lease_id,
        }

    def _download_dataframe_csv(self, project, head: dict[str, Any]) -> dict[str, Any]:
        if head.get('data_type') != 'pandas.DataFrame':
            raise InvalidRequestError('CSV downloads are only available for DataFrame artifacts.')
        self._validate_dataframe_download_size(head, label='CSV')
        frame = project.object_store.load_value(str(head['artifact_hash']), str(head['data_type']))
        csv_bytes = frame.to_csv(index=False).encode('utf-8')
        return {
            'kind': 'bytes',
            'content': csv_bytes,
            'filename': f'{self._sanitize_filename_stem(str(head.get("artifact_name") or "artifact"))}.csv',
            'mime_type': 'text/csv; charset=utf-8',
        }

    def _download_dataframe_xlsx(self, project, head: dict[str, Any]) -> dict[str, Any]:
        if head.get('data_type') != 'pandas.DataFrame':
            raise InvalidRequestError('XLSX downloads are only available for DataFrame artifacts.')
        self._validate_dataframe_download_size(head, label='XLSX')
        frame = project.object_store.load_value(str(head['artifact_hash']), str(head['data_type']))
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False, engine='openpyxl')
        return {
            'kind': 'bytes',
            'content': buffer.getvalue(),
            'filename': f'{self._sanitize_filename_stem(str(head.get("artifact_name") or "artifact"))}.xlsx',
            'mime_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }

    @staticmethod
    def _validate_dataframe_download_size(head: dict[str, Any], *, label: str) -> None:
        size_bytes = int(head.get('size_bytes') or 0)
        if size_bytes > DATAFRAME_CSV_DOWNLOAD_MAX_BYTES:
            raise InvalidRequestError(f'{label} downloads are limited to DataFrame artifacts no larger than 100 MB.')

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


def _resolve_constant_value(*, data_type: str, value: Any, value_json: str | None) -> Any:
    if value_json is not None:
        try:
            value = json.loads(value_json)
        except json.JSONDecodeError as exc:
            raise InvalidRequestError(f'Constant value must be valid JSON: {exc.msg}.') from exc
    if data_type == 'float' and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _xlsx_rows(content: bytes, sheet_name: str | int) -> list[list[Any]]:
    workbook = pd.ExcelFile(io.BytesIO(content))
    frame = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=object)
    return frame.where(frame.notna(), None).values.tolist()


def _validated_tabular_rows(raw_rows: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    if not raw_rows:
        raise InvalidRequestError('DataFrame upload must include a header row.')
    header_row = raw_rows[0]
    rightmost_header = max((index for index, value in enumerate(header_row) if _cell_is_nonempty(value)), default=-1)
    if rightmost_header < 0:
        raise InvalidRequestError('DataFrame upload must include a header with column names.')
    header = header_row[: rightmost_header + 1]
    index_column = not _cell_is_nonempty(header[0])
    column_names = [str(value).strip() for value in header[index_column:]]
    if any(not name for name in column_names):
        empty_column = next(index for index, name in enumerate(column_names, start=int(index_column)) if not name)
        raise InvalidRequestError(
            f'DataFrame header cell {_cell_name(1, empty_column + 1)} must have a nonempty column name.'
        )
    if len(set(column_names)) != len(column_names):
        duplicate = next(name for name in column_names if column_names.count(name) > 1)
        raise InvalidRequestError(f'DataFrame header has duplicate column name `{duplicate}`.')

    rows: list[list[Any]] = []
    for row_number, row in enumerate(raw_rows[1:], start=2):
        values = list(row)
        if not any(_cell_is_nonempty(value) for value in values):
            for remaining_row_number, remaining in enumerate(raw_rows[row_number:], start=row_number + 1):
                cell = _first_nonempty_cell(remaining)
                if cell is not None:
                    cell_name = _cell_name(remaining_row_number, cell + 1)
                    raise InvalidRequestError(
                        f'DataFrame upload contains data after its first empty row at {cell_name}.'
                    )
            break
        extra_cell = _first_nonempty_cell(values[rightmost_header + 1 :])
        if extra_cell is not None:
            cell_name = _cell_name(row_number, rightmost_header + extra_cell + 2)
            raise InvalidRequestError(f'DataFrame upload contains data to the right of its header at {cell_name}.')
        rows.append((values[: rightmost_header + 1] + [None] * (rightmost_header + 1))[: rightmost_header + 1])
    if index_column:
        rows = [row[1:] for row in rows]
    return column_names, rows


def _cell_is_nonempty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or value != '')


def _first_nonempty_cell(values: list[Any], offset: int = 0) -> int | None:
    return next((offset + index for index, value in enumerate(values) if _cell_is_nonempty(value)), None)


def _cell_name(row: int, column: int) -> str:
    letters = ''
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord('A') + remainder) + letters
    return f'{letters}{row}'
