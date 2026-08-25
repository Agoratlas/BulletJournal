from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(slots=True)
class SerializedAssetObject:
    object_role: str
    persisted: dict[str, Any]
    object_index: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class SerializedAssetVersion:
    asset_type: str
    interactive: bool
    definition: dict[str, Any]
    modifier_schema: list[dict[str, Any]]
    default_modifiers: dict[str, Any]
    objects: list[SerializedAssetObject]


def base_asset_definition(
    *,
    asset_type: str,
    interactive: bool,
    title: str,
    description: str | None,
    modifier_schema: list[dict[str, Any]],
    default_modifiers: dict[str, Any],
) -> dict[str, Any]:
    del modifier_schema, default_modifiers
    return {
        'asset_type': asset_type,
        'interactive': interactive,
        'display_title': title,
        'description': description,
    }


def dataset_modifier_schema(
    column_definitions: list[dict[str, Any]],
    default_modifiers: dict[str, Any],
    *,
    filters_targets: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            'id': 'page',
            'title': 'Page',
            'kind': 'page',
            'category': 'saved_query',
            'server_targets': ['table'],
            'default_value': default_modifiers['page'],
        },
        {
            'id': 'sort',
            'title': 'Sort',
            'kind': 'sort',
            'category': 'saved_query',
            'server_targets': ['table'],
            'default_value': default_modifiers['sort'],
            'columns': [
                {
                    'id': column['id'],
                    'title': column['title'],
                }
                for column in column_definitions
            ],
        },
        {
            'id': 'filters',
            'title': 'Filters',
            'kind': 'filters',
            'category': 'saved_query',
            'server_targets': filters_targets,
            'default_value': default_modifiers['filters'],
            'columns': column_definitions,
        },
        {
            'id': 'highlights',
            'title': 'Highlights',
            'kind': 'highlights',
            'category': 'saved_query',
            'server_targets': ['table'],
            'default_value': default_modifiers['highlights'],
            'columns': column_definitions,
        },
    ]


def axis_modifier_defaults(label: str) -> dict[str, Any]:
    return {
        'label_size': 12,
        'label': label,
        'hide_label': False,
        'tick_count': None,
        'tick_size': None,
        'show_grid_lines': True,
        'scale': 'lin',
    }


def title_modifier_defaults(text: str) -> dict[str, Any]:
    return {
        'size': 14,
        'text': text,
        'hide_title': True,
        'position': 'top',
    }


def dataframe_column_definitions(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            'id': str(column_name),
            'title': str(column_name),
            'data_type': str(dtype),
            'filter_kinds': filter_kinds_for_dtype(dtype),
        }
        for column_name, dtype in dataframe.dtypes.items()
    ]


def filter_kinds_for_dtype(dtype: Any) -> list[str]:
    if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_datetime64_any_dtype(dtype):
        return ['range', 'value']
    if pd.api.types.is_bool_dtype(dtype):
        return ['value']
    return ['value', 'regex']


def json_safe_modifier_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return json_safe_modifier_value(normalized)
    return str(value)
