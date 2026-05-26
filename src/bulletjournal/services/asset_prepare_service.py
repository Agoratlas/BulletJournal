from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from bulletjournal.domain.errors import InvalidRequestError, NotFoundError

_ALLOWED_PAGE_SIZES = {10, 25, 50, 100}
_DEFAULT_PAGE_SIZE = 25
_MAX_PREPARED_RESPONSE_BYTES = 1_000_000


class AssetPrepareService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def prepare_asset(
        self,
        node_id: str,
        asset_name: str,
        *,
        asset_version_id: int | None,
        modifier_overrides: dict[str, Any],
        transient_modifiers: dict[str, Any],
        panel_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del transient_modifiers, panel_context
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        current_asset_version_id = head.get('current_asset_version_id')
        if current_asset_version_id is None or head.get('definition') is None:
            raise InvalidRequestError(f'Asset `{node_id}/{asset_name}` has not been produced yet.')
        if head.get('asset_type') != 'dataframe':
            raise InvalidRequestError('Asset prepare is only supported for dataframe assets in this release.')
        if not isinstance(modifier_overrides, dict):
            raise InvalidRequestError('modifier_overrides must be an object.')
        errors: list[dict[str, str]] = []
        if asset_version_id is not None and asset_version_id != current_asset_version_id:
            errors.append(
                {
                    'code': 'asset_version_mismatch',
                    'message': (
                        f'Asset `{node_id}/{asset_name}` moved from version {asset_version_id} '
                        f'to version {current_asset_version_id}.'
                    ),
                }
            )
        project = self.project_service.require_project()
        dataset_object = _backing_dataset_object(head.get('objects'))
        project.state_db.touch_artifact_object(dataset_object['artifact_hash'])
        dataset_path = project.object_store.load_file_path(str(dataset_object['artifact_hash']))
        table_payload, resolved_modifiers = _prepare_dataframe_payload(
            dataset_path=dataset_path,
            definition=head['definition'],
            default_modifiers=head.get('default_modifiers') or {},
            modifier_overrides=modifier_overrides,
        )
        response = {
            'asset_version_id': int(current_asset_version_id),
            'state': head['state'],
            'resolved_modifiers': resolved_modifiers,
            'override_schema_hash': head.get('override_schema_hash'),
            'payloads': {'table': table_payload},
            'errors': errors,
        }
        if len(json.dumps(response, ensure_ascii=True).encode('utf-8')) > _MAX_PREPARED_RESPONSE_BYTES:
            raise InvalidRequestError('Prepared asset response exceeds the 1 MB cap.')
        return response


def _backing_dataset_object(objects: object) -> dict[str, Any]:
    if not isinstance(objects, list):
        raise InvalidRequestError('Asset backing dataset metadata is missing.')
    for item in objects:
        if isinstance(item, dict) and item.get('object_role') == 'backing_dataset' and item.get('artifact_hash'):
            return item
    raise InvalidRequestError('Asset backing dataset is missing.')


def _prepare_dataframe_payload(
    *,
    dataset_path: Path,
    definition: dict[str, Any],
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    frame = pl.scan_parquet(dataset_path)
    schema = frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    resolved_page = _resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = _resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    if resolved_sort:
        sort_entry = resolved_sort[0]
        frame = frame.sort(
            column_id_map[sort_entry['column']],
            descending=sort_entry['direction'] == 'desc',
            nulls_last=True,
        )
    page_index = resolved_page['index']
    page_size = resolved_page['size']
    rows = frame.slice(page_index * page_size, page_size).collect().to_dicts()
    rows_total = definition.get('row_count')
    if not isinstance(rows_total, int) or rows_total < 0:
        rows_total = len(pl.read_parquet(dataset_path))
    return {
        'kind': 'table',
        'rows_total': rows_total,
        'columns': [
            {
                'id': str(name),
                'title': str(name),
                'data_type': str(schema[name]),
                'sortable': True,
            }
            for name in column_names
        ],
        'page': resolved_page,
        'sort': resolved_sort,
        'rows': [{str(key): _json_safe_value(value) for key, value in row.items()} for row in rows],
    }, {
        'page': resolved_page,
        'sort': resolved_sort,
    }


def _resolve_page(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> dict[str, int]:
    default_page = default_modifiers.get('page') if isinstance(default_modifiers, dict) else None
    page_index = _coerce_page_index(default_page.get('index') if isinstance(default_page, dict) else 0)
    page_size = _coerce_page_size(default_page.get('size') if isinstance(default_page, dict) else _DEFAULT_PAGE_SIZE)
    if 'page' in modifier_overrides:
        page_override = modifier_overrides['page']
        if not isinstance(page_override, dict):
            raise InvalidRequestError('modifier_overrides.page must be an object.')
        page_index = _coerce_page_index(page_override.get('index', page_index))
        page_size = _coerce_page_size(page_override.get('size', page_size))
    return {'index': page_index, 'size': page_size}


def _resolve_sort(
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    column_id_map: dict[str, Any],
) -> list[dict[str, str]]:
    candidate = default_modifiers.get('sort') if isinstance(default_modifiers, dict) else []
    if 'sort' in modifier_overrides:
        candidate = modifier_overrides['sort']
    if candidate in (None, []):
        return []
    if not isinstance(candidate, list):
        raise InvalidRequestError('modifier_overrides.sort must be an array.')
    if len(candidate) > 1:
        raise InvalidRequestError('Only one active sort key is supported in this release.')
    if not candidate:
        return []
    entry = candidate[0]
    if not isinstance(entry, dict):
        raise InvalidRequestError('modifier_overrides.sort entries must be objects.')
    column = entry.get('column')
    direction = entry.get('direction')
    if not isinstance(column, str) or column not in column_id_map:
        raise InvalidRequestError(f'Unknown sort column `{column}`.')
    if direction not in {'asc', 'desc'}:
        raise InvalidRequestError('Sort direction must be `asc` or `desc`.')
    return [{'column': column, 'direction': direction}]


def _coerce_page_index(value: object) -> int:
    if not isinstance(value, int) or value < 0:
        raise InvalidRequestError('Page index must be a zero-based integer.')
    return value


def _coerce_page_size(value: object) -> int:
    if not isinstance(value, int) or value not in _ALLOWED_PAGE_SIZES:
        allowed = ', '.join(str(size) for size in sorted(_ALLOWED_PAGE_SIZES))
        raise InvalidRequestError(f'Page size must be one of: {allowed}.')
    return value


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return _json_safe_value(normalized)
    isoformat = getattr(value, 'isoformat', None)
    if callable(isoformat):
        try:
            return isoformat()
        except TypeError:
            pass
    return str(value)
