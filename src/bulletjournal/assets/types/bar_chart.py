from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
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
    axis_modifier_defaults,
    base_asset_definition,
    dataframe_column_definitions,
    dataset_modifier_schema,
    json_safe_modifier_value,
    title_modifier_defaults,
)
from bulletjournal.assets.types.pie_chart import (
    DEFAULT_PIE_CHART_COLOR,
    normalize_pie_chart_color_mapping,
    pie_chart_color_for_value,
    pie_chart_color_mapping_from_definition,
    validate_pie_chart_color,
)
from bulletjournal.assets.validation import (
    merge_nested_dicts,
    validate_axis_modifier_defaults,
    validate_modifier_defaults,
    validate_number,
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

DEFAULT_BAR_CHART_AGGREGATION = 'sum'
BAR_CHART_AGGREGATION_ALIASES = {
    'sum': 'sum',
    'mean': 'mean',
    'avg': 'mean',
    'average': 'mean',
    'count': 'count',
    'len': 'count',
    'size': 'count',
    'unique': 'unique',
    'nunique': 'unique',
    'min': 'min',
    'max': 'max',
    'median': 'median',
}
NUMERIC_BAR_CHART_AGGREGATIONS = {'sum', 'mean', 'min', 'max', 'median'}
BAR_CHART_AGGREGATION_TITLES = {
    'sum': 'Sum',
    'mean': 'Mean',
    'count': 'Count',
    'unique': 'Unique values',
    'min': 'Min',
    'max': 'Max',
    'median': 'Median',
}


@dataclass(slots=True, init=False)
class BarChart(BaseAsset):
    dataframe: pd.DataFrame
    category: str
    color: str | dict[object, str] | None
    value: str
    aggregation: str
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'bar_chart'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        category,
        color=None,
        value,
        aggregation: str = DEFAULT_BAR_CHART_AGGREGATION,
        **modifier_kwargs: Any,
    ) -> None:
        self.dataframe = dataframe
        self.category = category
        self.color = color
        self.value = value
        self.aggregation = aggregation
        self.modifier_defaults = modifier_kwargs or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Bar chart assets require a pandas.DataFrame payload.')
        if not isinstance(self.category, str) or not self.category:
            raise TypeError('Bar chart assets require `category` to be a non-empty column name.')
        if self.category not in self.dataframe.columns:
            raise ValueError(f'Bar chart category column `{self.category}` was not found in the provided DataFrame.')
        if not isinstance(self.value, str) or not self.value:
            raise TypeError('Bar chart assets require `value` to be a non-empty column name.')
        if self.value not in self.dataframe.columns:
            raise ValueError(f'Bar chart value column `{self.value}` was not found in the provided DataFrame.')
        validate_pie_chart_color(self.dataframe, category_column=self.category, color=self.color, label='Bar chart')
        validate_bar_chart_aggregation(self.dataframe, value_column=self.value, aggregation=self.aggregation)
        validate_bar_chart_modifier_defaults(self.modifier_defaults)


def validate_bar_chart_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={'bar_width', 'border_thickness', 'x_axis', 'y_axis', 'title'},
        context='Bar chart assets',
    )
    if value is None:
        return
    if 'bar_width' in value:
        validate_number(value['bar_width'], label='Bar chart modifier `bar_width`')
    if 'border_thickness' in value:
        validate_number(value['border_thickness'], label='Bar chart modifier `border_thickness`')
    if 'x_axis' in value:
        validate_axis_modifier_defaults(value['x_axis'], label='Bar chart modifier `x_axis`')
    if 'y_axis' in value:
        validate_axis_modifier_defaults(value['y_axis'], label='Bar chart modifier `y_axis`')
    if 'title' in value:
        validate_title_modifier_defaults(value['title'], label='Bar chart modifier `title`')


def validate_bar_chart_aggregation(dataframe: pd.DataFrame, *, value_column: str, aggregation: object) -> None:
    resolved = normalize_bar_chart_aggregation(aggregation)
    if resolved is None:
        supported = ', '.join(sorted(BAR_CHART_AGGREGATION_ALIASES))
        raise ValueError(f'Bar chart `aggregation` must be one of: {supported}.')
    if resolved in NUMERIC_BAR_CHART_AGGREGATIONS and not pd.api.types.is_numeric_dtype(dataframe[value_column]):
        raise TypeError(f'Bar chart aggregation `{resolved}` requires numeric values in column `{value_column}`.')


def normalize_bar_chart_aggregation(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return BAR_CHART_AGGREGATION_ALIASES.get(value.strip().lower())


def bar_chart_modifier_defaults(*, title: str, category_column: str, y_axis_label: str) -> dict[str, Any]:
    return {
        'bar_width': 90,
        'border_thickness': 0,
        'x_axis': axis_modifier_defaults(category_column),
        'y_axis': axis_modifier_defaults(y_axis_label),
        'title': title_modifier_defaults(title),
    }


def bar_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'id': 'bar_width',
            'title': 'Bar width',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['bar_width'],
            'min_value': 0,
            'max_value': 100,
            'step': 1,
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
            'id': 'x_axis',
            'title': 'X axis',
            'kind': 'chart_axis',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['x_axis'],
        },
        {
            'id': 'y_axis',
            'title': 'Y axis',
            'kind': 'chart_axis',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['y_axis'],
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


def serialize_bar_chart(
    asset: BarChart,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    normalized_aggregation = normalize_bar_chart_aggregation(asset.aggregation)
    if normalized_aggregation is None:
        raise InvalidRequestError('Bar chart asset definition is missing its aggregation mode.')
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
            bar_chart_modifier_defaults(
                title=title,
                category_column=str(asset.category),
                y_axis_label=bar_chart_y_axis_label(str(asset.value), normalized_aggregation),
            ),
            asset.modifier_defaults,
        ),
    }
    modifier_schema = [
        *dataset_modifier_schema(column_definitions, default_modifiers, filters_targets=['main', 'table']),
        *bar_chart_modifier_schema(default_modifiers),
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
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'table_columns': [str(column) for column in asset.dataframe.columns],
            'row_count': int(asset.dataframe.shape[0]),
            'bar_category_column': str(asset.category),
            'bar_value_column': str(asset.value),
            'bar_aggregation': normalized_aggregation,
            'bar_color_mapping': color_mapping,
            'bar_default_color': DEFAULT_PIE_CHART_COLOR,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def prepare_bar_chart(
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
    category_column = definition.get('bar_category_column')
    if not isinstance(category_column, str) or category_column not in column_id_map:
        raise InvalidRequestError('Bar chart asset definition is missing its category source column.')
    value_column = definition.get('bar_value_column')
    if not isinstance(value_column, str) or value_column not in column_id_map:
        raise InvalidRequestError('Bar chart asset definition is missing its value source column.')
    aggregation = normalize_bar_chart_aggregation(definition.get('bar_aggregation'))
    if aggregation is None:
        raise InvalidRequestError('Bar chart asset definition is missing its aggregation mode.')
    category_dtype = schema[column_id_map[category_column]]
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selected_categories = resolve_bar_chart_selected_categories(
        transient_modifiers,
        column=category_column,
        dtype=category_dtype,
    )
    table_frame = filtered_frame
    if selected_categories:
        table_frame = apply_bar_chart_selection(table_frame, category_column, selected_categories, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_bar_chart_main_payload(
            filtered_frame,
            category_column=category_column,
            value_column=value_column,
            aggregation=aggregation,
            column_id_map=column_id_map,
            color_mapping_entries=definition.get('bar_color_mapping'),
            default_color=definition.get('bar_default_color'),
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


def prepare_bar_chart_main_payload(
    frame: pl.LazyFrame,
    *,
    category_column: str,
    value_column: str,
    aggregation: str,
    column_id_map: dict[str, Any],
    color_mapping_entries: object,
    default_color: object,
) -> dict[str, Any]:
    category_name = column_id_map[category_column]
    value_name = column_id_map[value_column]
    explicit_color_mapping = pie_chart_color_mapping_from_definition(color_mapping_entries)
    resolved_default_color = (
        default_color if isinstance(default_color, str) and default_color else DEFAULT_PIE_CHART_COLOR
    )
    stats = frame.select(
        [
            pl.len().alias('rows_total'),
            pl.when(pl.col(category_name).is_not_null() & pl.col(value_name).is_not_null())
            .then(1)
            .otherwise(0)
            .sum()
            .alias('non_null_rows'),
        ]
    ).collect()
    rows_total = int(stats['rows_total'][0]) if stats.height else 0
    non_null_rows = int(stats['non_null_rows'][0]) if stats.height else 0
    if non_null_rows == 0:
        return {
            'kind': 'bar_chart',
            'category_column': category_column,
            'value_column': value_column,
            'aggregation': aggregation,
            'rows_total': rows_total,
            'non_null_rows': 0,
            'bars': [],
        }
    grouped = (
        frame.filter(pl.col(category_name).is_not_null() & pl.col(value_name).is_not_null())
        .group_by(category_name)
        .agg(bar_chart_aggregation_expr(value_name, aggregation))
        .sort(['aggregate_value', category_name], descending=[True, False])
        .collect()
    )
    bars: list[dict[str, Any]] = []
    for row in grouped.to_dicts():
        value = json_safe_value(row.get(category_name))
        if value is None:
            continue
        aggregate_value = json_safe_value(row.get('aggregate_value'))
        if aggregate_value is None:
            continue
        bars.append(
            {
                'value': value,
                'label': value if isinstance(value, str) else str(value),
                'aggregate_value': aggregate_value,
                'color': pie_chart_color_for_value(
                    value,
                    index=len(bars),
                    explicit_color_mapping=explicit_color_mapping,
                    default_color=resolved_default_color,
                ),
            }
        )
    return {
        'kind': 'bar_chart',
        'category_column': category_column,
        'value_column': value_column,
        'aggregation': aggregation,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'bars': bars,
    }


def bar_chart_aggregation_expr(column_name: str, aggregation: str) -> pl.Expr:
    if aggregation == 'sum':
        return pl.col(column_name).sum().alias('aggregate_value')
    if aggregation == 'mean':
        return pl.col(column_name).mean().alias('aggregate_value')
    if aggregation == 'count':
        return pl.col(column_name).count().alias('aggregate_value')
    if aggregation == 'unique':
        return pl.col(column_name).drop_nulls().n_unique().alias('aggregate_value')
    if aggregation == 'min':
        return pl.col(column_name).min().alias('aggregate_value')
    if aggregation == 'max':
        return pl.col(column_name).max().alias('aggregate_value')
    if aggregation == 'median':
        return pl.col(column_name).median().alias('aggregate_value')
    raise InvalidRequestError(f'Unsupported bar chart aggregation `{aggregation}`.')


def bar_chart_y_axis_label(value_column: str, aggregation: str) -> str:
    return f'{BAR_CHART_AGGREGATION_TITLES[aggregation]} of {value_column}'


def resolve_bar_chart_selected_categories(
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


def apply_bar_chart_selection(
    frame: pl.LazyFrame,
    column: str,
    selected_categories: list[Any],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    return frame.filter(
        pl.col(column_name).is_not_null() & pl.col(column_name).is_in(selected_categories).fill_null(False)
    )
