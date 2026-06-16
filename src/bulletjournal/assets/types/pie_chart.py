from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.category_ordering import (
    normalize_category_order_modifier_value,
    resolve_category_order,
    sort_category_rows,
    validate_category_order_value,
)
from bulletjournal.assets.prepare_utils import (
    coerce_filter_value,
    frame_with_filters,
    frame_with_sort,
    json_safe_value,
    prepared_table_payload,
    resolve_filters,
    resolve_page,
    resolve_sort,
)
from bulletjournal.assets.serialization import (
    SerializedAssetObject,
    SerializedAssetVersion,
    base_asset_definition,
    dataframe_column_definitions,
    dataset_modifier_schema,
    json_safe_modifier_value,
    title_modifier_defaults,
)
from bulletjournal.assets.validation import (
    merge_nested_dicts,
    validate_modifier_defaults,
    validate_number,
    validate_optional_asset_column,
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

DEFAULT_PIE_CHART_COLOR = '#94a3b8'
DEFAULT_PIE_CHART_CATEGORY_ORDER = 'value_desc'
DEFAULT_PIE_CHART_PALETTE = [
    '#2563eb',
    '#14b8a6',
    '#f59e0b',
    '#ef4444',
    '#8b5cf6',
    '#06b6d4',
    '#84cc16',
    '#f97316',
]


@dataclass(slots=True, init=False)
class PieChart(BaseAsset):
    dataframe: pd.DataFrame
    category: str
    color: str | dict[object, str] | None
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'pie_chart'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        category,
        color=None,
        **modifier_kwargs: Any,
    ) -> None:
        self.dataframe = dataframe
        self.category = category
        self.color = color
        self.modifier_defaults = modifier_kwargs or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Pie chart assets require a pandas.DataFrame payload.')
        if not isinstance(self.category, str) or not self.category:
            raise TypeError('Pie chart assets require `category` to be a non-empty column name.')
        if self.category not in self.dataframe.columns:
            raise ValueError(f'Pie chart category column `{self.category}` was not found in the provided DataFrame.')
        validate_pie_chart_color(self.dataframe, category_column=self.category, color=self.color)
        validate_pie_chart_modifier_defaults(self.modifier_defaults)


def validate_pie_chart_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={
            'inner_radius',
            'label_size',
            'label_threshold',
            'label_position',
            'merge_threshold',
            'border_thickness',
            'category_order',
            'merged_category_label',
            'show_merged_category',
            'show_percentages',
            'title',
        },
        context='Pie chart assets',
    )
    if value is None:
        return
    if 'inner_radius' in value:
        validate_number(value['inner_radius'], label='Pie chart modifier `inner_radius`')
        if float(value['inner_radius']) < 0 or float(value['inner_radius']) > 1:
            raise TypeError('Pie chart modifier `inner_radius` must be between 0 and 1.')
    if 'label_size' in value:
        validate_number(value['label_size'], label='Pie chart modifier `label_size`')
        if float(value['label_size']) < 1:
            raise TypeError('Pie chart modifier `label_size` must be positive.')
    if 'label_threshold' in value:
        validate_number(value['label_threshold'], label='Pie chart modifier `label_threshold`')
        if float(value['label_threshold']) < 0 or float(value['label_threshold']) > 100:
            raise TypeError('Pie chart modifier `label_threshold` must be between 0 and 100.')
    if 'label_position' in value:
        validate_number(value['label_position'], label='Pie chart modifier `label_position`')
        if float(value['label_position']) < 0 or float(value['label_position']) > 200:
            raise TypeError('Pie chart modifier `label_position` must be between 0 and 200.')
    if 'merge_threshold' in value:
        validate_number(value['merge_threshold'], label='Pie chart modifier `merge_threshold`')
        if float(value['merge_threshold']) < 0 or float(value['merge_threshold']) > 100:
            raise TypeError('Pie chart modifier `merge_threshold` must be between 0 and 100.')
    if 'border_thickness' in value:
        validate_number(value['border_thickness'], label='Pie chart modifier `border_thickness`')
        if float(value['border_thickness']) < 0:
            raise TypeError('Pie chart modifier `border_thickness` must be non-negative.')
    if 'category_order' in value:
        validate_category_order_value(value['category_order'], label='Pie chart modifier `category_order`')
    if 'merged_category_label' in value and not isinstance(value['merged_category_label'], str):
        raise TypeError('Pie chart modifier `merged_category_label` must be a string.')
    if 'show_merged_category' in value and not isinstance(value['show_merged_category'], bool):
        raise TypeError('Pie chart modifier `show_merged_category` must be a bool.')
    if 'show_percentages' in value and not isinstance(value['show_percentages'], bool):
        raise TypeError('Pie chart modifier `show_percentages` must be a bool.')
    if 'title' in value:
        validate_title_modifier_defaults(value['title'], label='Pie chart modifier `title`')


def validate_pie_chart_color(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color: str | dict[object, str] | None,
    label: str = 'Pie chart',
) -> None:
    if color is None:
        return
    if isinstance(color, str):
        validate_optional_asset_column(dataframe, color, label=f'{label} `color`')
        validate_pie_chart_color_column(
            dataframe,
            category_column=category_column,
            color_column=color,
            label=label,
        )
        return
    if not isinstance(color, dict):
        raise TypeError(f'{label} `color` must be a column name or a dict mapping categories to colors.')
    for key, value in color.items():
        if key is None:
            raise TypeError(f'{label} `color` mapping keys cannot be None.')
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f'{label} `color` mapping values must be non-empty strings.')


def validate_pie_chart_color_column(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color_column: str,
    label: str = 'Pie chart',
) -> None:
    color_pairs = dataframe[[category_column, color_column]]
    for category_value, group in color_pairs.groupby(category_column, dropna=True):
        distinct_colors = {value.item() if hasattr(value, 'item') else value for value in group[color_column].tolist()}
        if any(pd.isna(value) for value in distinct_colors):
            raise ValueError(
                f'{label} color column `{color_column}` contains missing colors for category `{category_value}`.'
            )
        if len(distinct_colors) > 1:
            raise ValueError(
                f'{label} color column `{color_column}` assigns multiple colors to category `{category_value}`.'
            )
        only_color = next(iter(distinct_colors), None)
        if not isinstance(only_color, str) or not only_color.strip():
            raise TypeError(
                f'{label} color column `{color_column}` must provide '
                f'non-empty string colors for category `{category_value}`.'
            )


def normalize_pie_chart_color_mapping(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color: str | dict[object, str] | None,
) -> list[tuple[Any, str]]:
    if color is None:
        return []
    if isinstance(color, dict):
        return [(normalize_pie_chart_value(key), value.strip()) for key, value in color.items()]
    color_pairs = dataframe[[category_column, color]].dropna(subset=[category_column])
    mapping: list[tuple[Any, str]] = []
    for category_value, group in color_pairs.groupby(category_column, dropna=True):
        distinct_colors = [value.item() if hasattr(value, 'item') else value for value in pd.unique(group[color])]
        only_color = distinct_colors[0] if distinct_colors else None
        if isinstance(only_color, str) and only_color.strip():
            mapping.append((normalize_pie_chart_value(category_value), only_color.strip()))
    return mapping


def normalize_pie_chart_value(value: Any) -> Any:
    return value.item() if hasattr(value, 'item') else value


def pie_chart_modifier_defaults(*, title: str, category_column: str) -> dict[str, Any]:
    del category_column
    return {
        'inner_radius': 0.5,
        'label_size': 20,
        'label_threshold': 5,
        'label_position': 102,
        'merge_threshold': 0,
        'border_thickness': 3,
        'category_order': DEFAULT_PIE_CHART_CATEGORY_ORDER,
        'merged_category_label': 'Others',
        'show_merged_category': True,
        'show_percentages': False,
        'title': title_modifier_defaults(title),
    }


def pie_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'id': 'inner_radius',
            'title': 'Inner radius',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['inner_radius'],
            'min_value': 0,
            'max_value': 1,
            'step': 0.05,
        },
        {
            'id': 'label_threshold',
            'title': 'Label threshold',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['label_threshold'],
            'min_value': 0,
            'max_value': 100,
            'step': 1,
        },
        {
            'id': 'label_size',
            'title': 'Label size',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['label_size'],
            'min_value': 1,
            'step': 1,
        },
        {
            'id': 'label_position',
            'title': 'Label position',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['label_position'],
            'min_value': 0,
            'max_value': 200,
            'step': 1,
        },
        {
            'id': 'merge_threshold',
            'title': 'Merge threshold',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['merge_threshold'],
            'min_value': 0,
            'max_value': 100,
            'step': 1,
        },
        {
            'id': 'category_order',
            'title': 'Category order',
            'kind': 'value',
            'category': 'saved_view',
            'server_targets': ['main'],
            'default_value': default_modifiers['category_order'],
        },
        {
            'id': 'border_thickness',
            'title': 'Border thickness',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['border_thickness'],
            'min_value': 0,
            'step': 0.5,
        },
        {
            'id': 'merged_category_label',
            'title': 'Merged category label',
            'kind': 'string',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['merged_category_label'],
        },
        {
            'id': 'show_merged_category',
            'title': 'Merged category visibility',
            'kind': 'bool',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['show_merged_category'],
        },
        {
            'id': 'show_percentages',
            'title': 'Show percentages',
            'kind': 'bool',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['show_percentages'],
        },
        {
            'id': 'title',
            'title': 'Title',
            'kind': 'chart_title',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['title'],
        },
    ]


def serialize_pie_chart(
    asset: PieChart,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    color_mapping = [
        {'value': json_safe_modifier_value(category_value), 'color': color_value}
        for category_value, color_value in normalize_pie_chart_color_mapping(
            asset.dataframe,
            category_column=str(asset.category),
            color=asset.color,
        )
    ]
    default_modifiers = {
        'page': {'index': 0, 'size': 10},
        'sort': [],
        'filters': [],
        **merge_nested_dicts(
            pie_chart_modifier_defaults(title=title, category_column=str(asset.category)),
            asset.modifier_defaults,
        ),
    }
    default_modifiers['category_order'] = normalize_category_order_modifier_value(
        default_modifiers.get('category_order', DEFAULT_PIE_CHART_CATEGORY_ORDER),
        label='Pie chart modifier `category_order`',
    )
    modifier_schema = [
        *dataset_modifier_schema(column_definitions, default_modifiers, filters_targets=['main', 'table']),
        *pie_chart_modifier_schema(default_modifiers),
        {
            'id': 'selected_categories',
            'title': 'Selected categories',
            'kind': 'value',
            'category': 'transient_view',
            'server_targets': ['table'],
            'default_value': [],
            'column': str(asset.category),
        },
    ]
    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=True,
        definition={
            **base_asset_definition(
                asset_type=asset.asset_type_id,
                interactive=True,
                title=title,
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'table_columns': [str(column) for column in asset.dataframe.columns],
            'row_count': int(asset.dataframe.shape[0]),
            'pie_category_column': str(asset.category),
            'pie_color_mapping': color_mapping,
            'pie_default_color': DEFAULT_PIE_CHART_COLOR,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def prepare_pie_chart(
    *,
    dataset_path: Path,
    definition: dict[str, Any],
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    transient_modifiers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    frame = pl.scan_parquet(dataset_path)
    schema = frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    category_column = definition.get('pie_category_column')
    if not isinstance(category_column, str) or category_column not in column_id_map:
        raise InvalidRequestError('Pie chart asset definition is missing its category source column.')
    category_dtype = schema[column_id_map[category_column]]
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_category_order = resolve_category_order(
        default_modifiers,
        modifier_overrides,
        default_mode=DEFAULT_PIE_CHART_CATEGORY_ORDER,
        column=category_column,
        dtype=category_dtype,
    )
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selected_categories = resolve_pie_chart_selected_categories(
        transient_modifiers,
        column=category_column,
        dtype=category_dtype,
    )
    table_frame = filtered_frame
    if selected_categories:
        table_frame = apply_pie_chart_selection(table_frame, category_column, selected_categories, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_pie_chart_main_payload(
            filtered_frame,
            column=category_column,
            column_id_map=column_id_map,
            color_mapping_entries=definition.get('pie_color_mapping'),
            category_order=resolved_category_order,
            default_color=definition.get('pie_default_color'),
        ),
        'table': prepared_table_payload(
            table_frame,
            schema=schema,
            column_names=column_names,
            resolved_page=resolved_page,
            resolved_sort=resolved_sort,
        ),
    }, {
        'page': resolved_page,
        'sort': resolved_sort,
        'filters': resolved_filters,
    }


def prepare_pie_chart_main_payload(
    frame: pl.LazyFrame,
    *,
    column: str,
    column_id_map: dict[str, Any],
    color_mapping_entries: object,
    category_order: str | list[Any],
    default_color: object,
) -> dict[str, Any]:
    column_name = column_id_map[column]
    explicit_color_mapping = pie_chart_color_mapping_from_definition(color_mapping_entries)
    resolved_default_color = (
        default_color if isinstance(default_color, str) and default_color else DEFAULT_PIE_CHART_COLOR
    )
    stats = frame.select([pl.len().alias('rows_total'), pl.col(column_name).count().alias('non_null_rows')]).collect()
    rows_total = int(stats['rows_total'][0]) if stats.height else 0
    non_null_rows = int(stats['non_null_rows'][0]) if stats.height else 0
    if non_null_rows == 0:
        return {
            'kind': 'pie_chart',
            'category_column': column,
            'rows_total': rows_total,
            'non_null_rows': 0,
            'slices': [],
        }
    grouped = (
        frame.filter(pl.col(column_name).is_not_null()).group_by(column_name).agg(pl.len().alias('count')).collect()
    )
    slices: list[dict[str, Any]] = []
    grouped_rows = sort_category_rows(
        grouped.to_dicts(),
        category_field=column_name,
        value_field='count',
        default_mode=DEFAULT_PIE_CHART_CATEGORY_ORDER,
        category_order=category_order,
    )
    for row in grouped_rows:
        value = json_safe_value(row.get(column_name))
        if value is None:
            continue
        count = int(row['count'])
        slices.append(
            {
                'value': value,
                'label': value if isinstance(value, str) else str(value),
                'count': count,
                'share': count / non_null_rows,
                'color': pie_chart_color_for_value(
                    value,
                    index=len(slices),
                    explicit_color_mapping=explicit_color_mapping,
                    default_color=resolved_default_color,
                ),
            }
        )
    return {
        'kind': 'pie_chart',
        'category_column': column,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'slices': slices,
    }


def pie_chart_color_mapping_from_definition(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    mapping: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        color = entry.get('color')
        if not isinstance(color, str) or not color:
            continue
        key = pie_chart_color_mapping_key(json_safe_value(entry.get('value')))
        mapping[key] = color
    return mapping


def pie_chart_color_for_value(
    value: object,
    *,
    index: int,
    explicit_color_mapping: dict[str, str],
    default_color: str,
) -> str:
    key = pie_chart_color_mapping_key(value)
    if explicit_color_mapping:
        return explicit_color_mapping.get(key, default_color)
    return DEFAULT_PIE_CHART_PALETTE[index % len(DEFAULT_PIE_CHART_PALETTE)]


def pie_chart_color_mapping_key(value: object) -> str:
    return json.dumps(json_safe_value(value), ensure_ascii=True, sort_keys=True)


def resolve_pie_chart_selected_categories(
    transient_modifiers: dict[str, Any],
    *,
    column: str,
    dtype: pl.DataType,
) -> list[Any]:
    candidate = transient_modifiers.get('selected_categories') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, []):
        return []
    if not isinstance(candidate, list):
        raise InvalidRequestError('transient_modifiers.selected_categories must be an array.')
    resolved: list[Any] = []
    seen_keys: set[str] = set()
    for value in candidate:
        coerced = coerce_filter_value(dtype, value, column=column, kind='selected_categories')
        if coerced is None:
            raise InvalidRequestError('transient_modifiers.selected_categories cannot contain null values.')
        value_key = json.dumps(json_safe_value(coerced), ensure_ascii=True, sort_keys=True)
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        resolved.append(coerced)
    return resolved


def apply_pie_chart_selection(
    frame: pl.LazyFrame,
    column: str,
    selected_categories: list[Any],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    return frame.filter(
        pl.col(column_name).is_not_null() & pl.col(column_name).is_in(selected_categories).fill_null(False)
    )
