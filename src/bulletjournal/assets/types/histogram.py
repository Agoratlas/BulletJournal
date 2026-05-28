from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.prepare_utils import (
    dtype_category,
    frame_with_filters,
    frame_with_sort,
    json_safe_value,
    numeric_plot_domain,
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
    title_modifier_defaults,
)
from bulletjournal.assets.validation import (
    merge_nested_dicts,
    validate_axis_modifier_defaults,
    validate_modifier_defaults,
    validate_number,
    validate_optional_asset_column,
    validate_positive_int,
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

DEFAULT_HISTOGRAM_BIN_COUNT = 20
MAX_HISTOGRAM_BIN_COUNT = 100


@dataclass(slots=True, init=False)
class Histogram(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    bins: int
    shape: str | None
    size: str | None
    color: str | None
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'histogram'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        x,
        bins: int = DEFAULT_HISTOGRAM_BIN_COUNT,
        shape=None,
        size=None,
        color=None,
        **modifier_kwargs: Any,
    ) -> None:
        if 'bin_count' in modifier_kwargs:
            bin_count = modifier_kwargs.pop('bin_count')
            if not isinstance(bin_count, int):
                raise TypeError('Histogram modifier `bin_count` must be an int.')
            if bins != DEFAULT_HISTOGRAM_BIN_COUNT and bins != bin_count:
                raise TypeError('Histogram `bins` and modifier `bin_count` must match when both are provided.')
            bins = bin_count
        self.dataframe = dataframe
        self.x = x
        self.bins = bins
        self.shape = shape
        self.size = size
        self.color = color
        self.modifier_defaults = modifier_kwargs or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Histogram assets require a pandas.DataFrame payload.')
        if not isinstance(self.x, str) or not self.x:
            raise TypeError('Histogram assets require `x` to be a non-empty column name.')
        if self.x not in self.dataframe.columns:
            raise ValueError(f'Histogram column `{self.x}` was not found in the provided DataFrame.')
        if not pd.api.types.is_numeric_dtype(self.dataframe[self.x]):
            raise TypeError(f'Histogram column `{self.x}` must use a numeric dtype.')
        if not isinstance(self.bins, int) or self.bins < 1:
            raise TypeError('Histogram assets require `bins` to be a positive integer.')
        validate_optional_asset_column(self.dataframe, self.shape, label='Histogram `shape`')
        validate_optional_asset_column(self.dataframe, self.size, label='Histogram `size`')
        validate_optional_asset_column(self.dataframe, self.color, label='Histogram `color`')
        validate_histogram_modifier_defaults(self.modifier_defaults)


def validate_histogram_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={'bin_count', 'bar_width', 'border_thickness', 'x_axis', 'y_axis', 'title'},
        context='Histogram assets',
    )
    if value is None:
        return
    if 'bin_count' in value:
        validate_positive_int(value['bin_count'], label='Histogram modifier `bin_count`')
    if 'bar_width' in value:
        validate_number(value['bar_width'], label='Histogram modifier `bar_width`')
    if 'border_thickness' in value:
        validate_number(value['border_thickness'], label='Histogram modifier `border_thickness`')
    if 'x_axis' in value:
        validate_axis_modifier_defaults(value['x_axis'], label='Histogram modifier `x_axis`')
    if 'y_axis' in value:
        validate_axis_modifier_defaults(value['y_axis'], label='Histogram modifier `y_axis`')
    if 'title' in value:
        validate_title_modifier_defaults(value['title'], label='Histogram modifier `title`')


def histogram_chart_modifier_defaults(
    *, title: str, x_column: str, y_axis_label: str, bin_count: int
) -> dict[str, Any]:
    return {
        'bin_count': bin_count,
        'bar_width': 90,
        'border_thickness': 0,
        'x_axis': axis_modifier_defaults(x_column),
        'y_axis': axis_modifier_defaults(y_axis_label),
        'title': title_modifier_defaults(title),
    }


def histogram_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'id': 'bin_count',
            'title': 'Bin count',
            'kind': 'int',
            'category': 'saved_view',
            'server_targets': ['main'],
            'default_value': default_modifiers['bin_count'],
            'min_value': 1,
            'max_value': 100,
            'step': 1,
        },
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


def serialize_histogram(
    asset: Histogram,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    encodings = {
        'x': {
            'column': str(asset.x),
            'data_type': str(asset.dataframe.dtypes[asset.x]),
            'kind': 'quantitative_binned',
        },
        'y': {
            'aggregate': 'count',
            'kind': 'quantitative',
        },
    }
    if asset.shape is not None:
        encodings['shape'] = {
            'column': str(asset.shape),
            'data_type': str(asset.dataframe.dtypes[asset.shape]),
            'kind': 'nominal',
        }
    if asset.size is not None:
        size_dtype = asset.dataframe.dtypes[asset.size]
        encodings['size'] = {
            'column': str(asset.size),
            'data_type': str(size_dtype),
            'kind': 'quantitative' if pd.api.types.is_numeric_dtype(size_dtype) else 'nominal',
        }
    if asset.color is not None:
        color_dtype = asset.dataframe.dtypes[asset.color]
        encodings['color'] = {
            'column': str(asset.color),
            'data_type': str(color_dtype),
            'kind': 'quantitative' if pd.api.types.is_numeric_dtype(color_dtype) else 'nominal',
        }
    default_modifiers = {
        'page': {'index': 0, 'size': 10},
        'sort': [],
        'filters': [],
        **merge_nested_dicts(
            histogram_chart_modifier_defaults(
                title=title,
                x_column=str(asset.x),
                y_axis_label='Rows',
                bin_count=int(asset.bins),
            ),
            asset.modifier_defaults,
        ),
    }
    modifier_schema = [
        *dataset_modifier_schema(column_definitions, default_modifiers, filters_targets=['main', 'table']),
        *histogram_chart_modifier_schema(default_modifiers),
        {
            'id': 'selection_range',
            'title': 'Selected range',
            'kind': 'range',
            'category': 'transient_view',
            'server_targets': ['table'],
            'default_value': None,
            'column': str(asset.x),
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
            'supports_table_view': True,
            'interaction_bindings': [
                {
                    'modifier_id': 'selection_range',
                    'source': 'vega_signal',
                    'signal_name': 'selection_range_start',
                    'category': 'transient_view',
                    'server_targets': ['table'],
                }
            ],
            'data_dependencies': ['backing_dataset'],
            'table_columns': [str(column) for column in asset.dataframe.columns],
            'table_column_types': {column['id']: column['data_type'] for column in column_definitions},
            'row_count': int(asset.dataframe.shape[0]),
            'dataset_binding': {'object_role': 'backing_dataset'},
            'encodings': encodings,
            'visual_defaults': {
                'bin_count': int(asset.bins),
                'y_scale_type': 'linear',
                'bar_corner_radius': 3,
            },
            'vega_template_kind': 'histogram',
            'histogram_column': str(asset.x),
            'histogram_column_type': str(asset.dataframe.dtypes[asset.x]),
            'histogram_shape_column': str(asset.shape) if asset.shape is not None else None,
            'histogram_shape_column_type': str(asset.dataframe.dtypes[asset.shape])
            if asset.shape is not None
            else None,
            'histogram_size_column': str(asset.size) if asset.size is not None else None,
            'histogram_size_column_type': str(asset.dataframe.dtypes[asset.size]) if asset.size is not None else None,
            'histogram_color_column': str(asset.color) if asset.color is not None else None,
            'histogram_color_column_type': str(asset.dataframe.dtypes[asset.color])
            if asset.color is not None
            else None,
            'default_bin_count': int(asset.bins),
            'object_role': 'backing_dataset',
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def prepare_histogram(
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
    histogram_column = definition.get('histogram_column')
    if not isinstance(histogram_column, str) or histogram_column not in column_id_map:
        raise InvalidRequestError('Histogram asset definition is missing its source column.')
    histogram_dtype = schema[column_id_map[histogram_column]]
    if dtype_category(histogram_dtype) != 'numeric':
        raise InvalidRequestError(f'Histogram column `{histogram_column}` must be numeric.')
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_bin_count = resolve_histogram_bin_count(default_modifiers, modifier_overrides)
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selection_ranges = resolve_histogram_selection_ranges(
        transient_modifiers,
        column=histogram_column,
        dtype=histogram_dtype,
    )
    table_frame = filtered_frame
    if selection_ranges:
        table_frame = apply_histogram_selections(table_frame, histogram_column, selection_ranges, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_histogram_main_payload(
            filtered_frame,
            column=histogram_column,
            column_id_map=column_id_map,
            bin_count=resolved_bin_count,
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
        'bin_count': resolved_bin_count,
    }


def prepare_histogram_main_payload(
    frame: pl.LazyFrame,
    *,
    column: str,
    column_id_map: dict[str, Any],
    bin_count: int,
) -> dict[str, Any]:
    column_name = column_id_map[column]
    stats = frame.select(
        [
            pl.len().alias('rows_total'),
            pl.col(column_name).count().alias('non_null_rows'),
            pl.col(column_name).min().alias('min_value'),
            pl.col(column_name).max().alias('max_value'),
        ]
    ).collect()
    rows_total = int(stats['rows_total'][0]) if stats.height else 0
    non_null_rows = int(stats['non_null_rows'][0]) if stats.height else 0
    if non_null_rows == 0:
        return {
            'kind': 'histogram',
            'x_column': column,
            'rows_total': rows_total,
            'non_null_rows': 0,
            'bin_count': bin_count,
            'domain': None,
            'bins': [],
        }
    min_value = float(stats['min_value'][0])
    max_value = float(stats['max_value'][0])
    if not math.isfinite(min_value) or not math.isfinite(max_value):
        raise InvalidRequestError(f'Histogram column `{column}` contains non-finite numeric values.')
    if min_value == max_value:
        return {
            'kind': 'histogram',
            'x_column': column,
            'rows_total': rows_total,
            'non_null_rows': non_null_rows,
            'bin_count': 1,
            'domain': {'min': json_safe_value(min_value - 0.5), 'max': json_safe_value(max_value + 0.5)},
            'bins': [
                {
                    'index': 0,
                    'start': json_safe_value(min_value - 0.5),
                    'end': json_safe_value(max_value + 0.5),
                    'count': non_null_rows,
                }
            ],
        }
    bin_width = (max_value - min_value) / bin_count
    if not math.isfinite(bin_width) or bin_width <= 0:
        raise InvalidRequestError(f'Histogram column `{column}` could not be binned safely.')
    grouped = (
        frame.filter(pl.col(column_name).is_not_null())
        .with_columns(
            (((pl.col(column_name) - min_value) / bin_width).floor().clip(0, bin_count - 1).cast(pl.Int64)).alias(
                '__histogram_bin_index'
            )
        )
        .group_by('__histogram_bin_index')
        .agg(pl.len().alias('count'))
        .sort('__histogram_bin_index')
        .collect()
    )
    counts_by_index = {int(row['__histogram_bin_index']): int(row['count']) for row in grouped.to_dicts()}
    bins: list[dict[str, Any]] = []
    for index in range(bin_count):
        start = min_value + (bin_width * index)
        end = max_value if index == bin_count - 1 else min_value + (bin_width * (index + 1))
        bins.append(
            {
                'index': index,
                'start': json_safe_value(start),
                'end': json_safe_value(end),
                'count': counts_by_index.get(index, 0),
            }
        )
    return {
        'kind': 'histogram',
        'x_column': column,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'bin_count': bin_count,
        'domain': numeric_plot_domain(
            min_value=min_value, max_value=max_value, column=column, context='Histogram column'
        ),
        'bins': bins,
    }


def resolve_histogram_bin_count(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> int:
    candidate = (
        default_modifiers.get('bin_count', DEFAULT_HISTOGRAM_BIN_COUNT)
        if isinstance(default_modifiers, dict)
        else DEFAULT_HISTOGRAM_BIN_COUNT
    )
    if 'bin_count' in modifier_overrides:
        candidate = modifier_overrides['bin_count']
    return coerce_bin_count(candidate)


def resolve_histogram_selection_ranges(
    transient_modifiers: dict[str, Any],
    *,
    column: str,
    dtype: pl.DataType,
) -> list[dict[str, int | float]]:
    if not isinstance(transient_modifiers, dict):
        return []
    if 'selection_ranges' in transient_modifiers:
        candidate = transient_modifiers.get('selection_ranges')
        if candidate in (None, []):
            return []
        if not isinstance(candidate, list):
            raise InvalidRequestError('transient_modifiers.selection_ranges must be an array.')
        raw_ranges = candidate
    else:
        legacy_candidate = transient_modifiers.get('selection_range')
        if legacy_candidate in (None, {}):
            return []
        if not isinstance(legacy_candidate, dict):
            raise InvalidRequestError('transient_modifiers.selection_range must be an object.')
        raw_ranges = [legacy_candidate]
    if dtype_category(dtype) != 'numeric':
        raise InvalidRequestError(f'Histogram selections are only supported for numeric columns such as `{column}`.')
    resolved_ranges: list[dict[str, int | float]] = []
    for entry in raw_ranges:
        if not isinstance(entry, dict):
            raise InvalidRequestError('transient_modifiers.selection_ranges entries must be objects.')
        lower = coerce_numeric_selection_value(entry.get('lower'), column=column)
        upper = coerce_numeric_selection_value(entry.get('upper'), column=column)
        if lower > upper:
            lower, upper = upper, lower
        resolved_ranges.append({'lower': lower, 'upper': upper})
    return resolved_ranges


def apply_histogram_selections(
    frame: pl.LazyFrame,
    column: str,
    selection_ranges: list[dict[str, int | float]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    predicate = pl.lit(False)
    for selection_range in selection_ranges:
        predicate = predicate | (
            (pl.col(column_name) >= selection_range['lower']) & (pl.col(column_name) <= selection_range['upper'])
        )
    return frame.filter(pl.col(column_name).is_not_null() & predicate)


def coerce_bin_count(value: object) -> int:
    if not isinstance(value, int) or value < 1 or value > MAX_HISTOGRAM_BIN_COUNT:
        raise InvalidRequestError(f'Histogram bin count must be an integer between 1 and {MAX_HISTOGRAM_BIN_COUNT}.')
    return value


def coerce_numeric_selection_value(value: object, *, column: str) -> int | float:
    if isinstance(value, bool):
        raise InvalidRequestError(f'Filter `selection_range` for column `{column}` expects numeric values.')
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InvalidRequestError(f'Filter `selection_range` for column `{column}` expects numeric values.')
