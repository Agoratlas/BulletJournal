from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

DEFAULT_HISTOGRAM_BIN_COUNT = 20
MAX_HISTOGRAM_BIN_COUNT = 100
DEFAULT_HISTOGRAM_TIME_GRANULARITY = 'auto'
HISTOGRAM_TIME_GRANULARITIES = ('auto', 'year', 'month', 'week', 'day', 'hour')
TEMPORAL_TIME_GRANULARITIES = ('year', 'month', 'week', 'day', 'hour')


@dataclass(slots=True, init=False)
class Histogram(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    bin_count: int | None
    time_granularity: str
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'histogram'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        x,
        bin_count: int | None = None,
        granularity: str = DEFAULT_HISTOGRAM_TIME_GRANULARITY,
        **modifier_kwargs: Any,
    ) -> None:
        if 'bins' in modifier_kwargs:
            raise TypeError('Histogram assets do not support a `bins` argument. Use `bin_count` instead.')
        unsupported_encodings = sorted(set(modifier_kwargs) & {'shape', 'size', 'color'})
        if unsupported_encodings:
            joined = ', '.join(f'`{key}`' for key in unsupported_encodings)
            raise TypeError(f'Histogram assets do not support {joined} arguments.')
        self.dataframe = dataframe
        self.x = x
        self.bin_count = DEFAULT_HISTOGRAM_BIN_COUNT if bin_count is None else bin_count
        self.time_granularity = granularity
        self.modifier_defaults = modifier_kwargs or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Histogram assets require a pandas.DataFrame payload.')
        if not isinstance(self.x, str) or not self.x:
            raise TypeError('Histogram assets require `x` to be a non-empty column name.')
        if self.x not in self.dataframe.columns:
            raise ValueError(f'Histogram column `{self.x}` was not found in the provided DataFrame.')
        series = self.dataframe[self.x]
        if pd.api.types.is_numeric_dtype(series):
            if not isinstance(self.bin_count, int) or self.bin_count < 1:
                raise TypeError('Histogram assets require `bin_count` to be a positive integer.')
        else:
            category = _temporal_series_category(series)
            if category is None:
                raise TypeError(f'Histogram column `{self.x}` must use a numeric, date, or datetime dtype.')
            if self.time_granularity not in HISTOGRAM_TIME_GRANULARITIES:
                allowed = ', '.join(HISTOGRAM_TIME_GRANULARITIES)
                raise TypeError(f'Histogram `granularity` must be one of: {allowed}.')
            if self.time_granularity != 'auto' and self.time_granularity not in supported_histogram_time_granularities(
                category
            ):
                raise TypeError(
                    f'Histogram granularity `{self.time_granularity}` is not supported for {category} columns.'
                )
        validate_histogram_modifier_defaults(self.modifier_defaults)


def validate_histogram_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={'bar_width', 'border_thickness', 'x_axis', 'y_axis', 'title'},
        context='Histogram assets',
    )
    if value is None:
        return
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
    *, title: str, x_column: str, y_axis_label: str, bin_count: int | None, time_granularity: str | None
) -> dict[str, Any]:
    defaults = {
        'bar_width': 90,
        'border_thickness': 0,
        'x_axis': axis_modifier_defaults(x_column),
        'y_axis': axis_modifier_defaults(y_axis_label),
        'title': title_modifier_defaults(title),
    }
    if bin_count is not None:
        defaults['bin_count'] = bin_count
        return defaults
    defaults['granularity'] = time_granularity or DEFAULT_HISTOGRAM_TIME_GRANULARITY
    defaults['x_axis'] = {**defaults['x_axis'], 'tick_count': 20}
    return defaults


def histogram_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    histogram_settings: list[dict[str, Any]] = []
    if 'bin_count' in default_modifiers:
        histogram_settings.append(
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
            }
        )
    else:
        histogram_settings.append(
            {
                'id': 'granularity',
                'title': 'Granularity',
                'kind': 'enum',
                'category': 'saved_view',
                'server_targets': ['main'],
                'default_value': default_modifiers['granularity'],
                'options': list(HISTOGRAM_TIME_GRANULARITIES),
            }
        )
    return [
        *histogram_settings,
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
    histogram_category = histogram_value_category(asset.dataframe[asset.x])
    default_bin_count = (
        int(asset.bin_count) if histogram_category == 'numeric' and asset.bin_count is not None else None
    )
    default_time_granularity = asset.time_granularity if histogram_category in {'date', 'datetime'} else None
    default_modifiers = {
        'page': {'index': 0, 'size': 10},
        'sort': [],
        'filters': [],
        **merge_nested_dicts(
            histogram_chart_modifier_defaults(
                title=title,
                x_column=str(asset.x),
                y_axis_label='Rows',
                bin_count=default_bin_count,
                time_granularity=default_time_granularity,
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
                title=title,
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'table_columns': [str(column) for column in asset.dataframe.columns],
            'row_count': int(asset.dataframe.shape[0]),
            'histogram_column': str(asset.x),
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
    histogram_category = dtype_category(histogram_dtype)
    if histogram_category not in {'numeric', 'date', 'datetime'}:
        raise InvalidRequestError(f'Histogram column `{histogram_column}` must be numeric, date, or datetime.')
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
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
    if histogram_category == 'numeric':
        resolved_bucket_modifiers = {'bin_count': resolve_histogram_bin_count(default_modifiers, modifier_overrides)}
        main_payload = prepare_histogram_main_payload(
            filtered_frame,
            column=histogram_column,
            column_id_map=column_id_map,
            bin_count=resolved_bucket_modifiers['bin_count'],
        )
    else:
        resolved_bucket_modifiers = {
            'granularity': resolve_histogram_time_granularity(default_modifiers, modifier_overrides)
        }
        main_payload = prepare_temporal_histogram_main_payload(
            filtered_frame,
            column=histogram_column,
            column_id_map=column_id_map,
            time_granularity=resolved_bucket_modifiers['granularity'],
            histogram_category=histogram_category,
        )
    return {
        'main': main_payload,
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
        **resolved_bucket_modifiers,
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


def resolve_histogram_time_granularity(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> str:
    candidate = (
        default_modifiers.get('granularity', DEFAULT_HISTOGRAM_TIME_GRANULARITY)
        if isinstance(default_modifiers, dict)
        else DEFAULT_HISTOGRAM_TIME_GRANULARITY
    )
    if 'granularity' in modifier_overrides:
        candidate = modifier_overrides['granularity']
    return coerce_histogram_time_granularity(candidate)


def resolve_histogram_selection_ranges(
    transient_modifiers: dict[str, Any],
    *,
    column: str,
    dtype: pl.DataType,
) -> list[dict[str, int | float | date | datetime]]:
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
    category = dtype_category(dtype)
    if category not in {'numeric', 'date', 'datetime'}:
        raise InvalidRequestError(
            f'Histogram selections are only supported for numeric and temporal columns such as `{column}`.'
        )
    resolved_ranges: list[dict[str, int | float | date | datetime]] = []
    for entry in raw_ranges:
        if not isinstance(entry, dict):
            raise InvalidRequestError('transient_modifiers.selection_ranges entries must be objects.')
        if category == 'numeric':
            lower = coerce_numeric_selection_value(entry.get('lower'), column=column)
            upper = coerce_numeric_selection_value(entry.get('upper'), column=column)
        else:
            lower = coerce_temporal_selection_value(entry.get('lower'), column=column, dtype=dtype)
            upper = coerce_temporal_selection_value(entry.get('upper'), column=column, dtype=dtype)
        if lower > upper:
            lower, upper = upper, lower
        resolved_ranges.append({'lower': lower, 'upper': upper})
    return resolved_ranges


def apply_histogram_selections(
    frame: pl.LazyFrame,
    column: str,
    selection_ranges: list[dict[str, int | float | date | datetime]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    predicate = pl.lit(False)
    for selection_range in selection_ranges:
        upper_operator = pl.col(column_name) <= selection_range['upper']
        if isinstance(selection_range['lower'], date | datetime):
            upper_operator = pl.col(column_name) < selection_range['upper']
        predicate = predicate | ((pl.col(column_name) >= selection_range['lower']) & upper_operator)
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


def histogram_value_category(series: pd.Series) -> str:
    if pd.api.types.is_numeric_dtype(series):
        return 'numeric'
    category = _temporal_series_category(series)
    if category is None:
        raise TypeError('Histogram columns must use a numeric, date, or datetime dtype.')
    return category


def prepare_temporal_histogram_main_payload(
    frame: pl.LazyFrame,
    *,
    column: str,
    column_id_map: dict[str, Any],
    time_granularity: str,
    histogram_category: str,
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
            'bin_count': 0,
            'domain': None,
            'bins': [],
            'x_value_kind': 'temporal',
            'time_granularity': fallback_histogram_time_granularity(time_granularity, histogram_category),
        }
    min_value = stats['min_value'][0]
    max_value = stats['max_value'][0]
    actual_time_granularity = effective_histogram_time_granularity(
        time_granularity, min_value=min_value, max_value=max_value, category=histogram_category
    )
    bin_start = floor_temporal_value(min_value, actual_time_granularity)
    max_bin_start = floor_temporal_value(max_value, actual_time_granularity)
    final_end = advance_temporal_value(max_bin_start, actual_time_granularity)
    grouped = (
        frame.filter(pl.col(column_name).is_not_null())
        .with_columns(
            pl.col(column_name)
            .dt.truncate(polars_granularity_every(actual_time_granularity))
            .alias('__histogram_bin_start')
        )
        .group_by('__histogram_bin_start')
        .agg(pl.len().alias('count'))
        .sort('__histogram_bin_start')
        .collect()
    )
    counts_by_start = {row['__histogram_bin_start']: int(row['count']) for row in grouped.to_dicts()}
    bins: list[dict[str, Any]] = []
    cursor = bin_start
    index = 0
    while cursor < final_end:
        next_cursor = advance_temporal_value(cursor, actual_time_granularity)
        bins.append(
            {
                'index': index,
                'start': temporal_value_to_epoch_ms(cursor),
                'end': temporal_value_to_epoch_ms(next_cursor),
                'count': counts_by_start.get(cursor, 0),
                'label': format_histogram_bin_label(cursor, next_cursor, actual_time_granularity),
            }
        )
        cursor = next_cursor
        index += 1
    return {
        'kind': 'histogram',
        'x_column': column,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'bin_count': len(bins),
        'domain': {
            'min': temporal_value_to_epoch_ms(bin_start),
            'max': temporal_value_to_epoch_ms(final_end),
        },
        'bins': bins,
        'x_value_kind': 'temporal',
        'time_granularity': actual_time_granularity,
    }


def coerce_histogram_time_granularity(value: object) -> str:
    if not isinstance(value, str) or value not in HISTOGRAM_TIME_GRANULARITIES:
        allowed = ', '.join(HISTOGRAM_TIME_GRANULARITIES)
        raise InvalidRequestError(f'Histogram granularity must be one of: {allowed}.')
    return value


def effective_histogram_time_granularity(
    time_granularity: str, *, min_value: date | datetime, max_value: date | datetime, category: str
) -> str:
    supported_time_granularities = supported_histogram_time_granularities(category)
    if time_granularity != 'auto':
        if time_granularity not in supported_time_granularities:
            raise InvalidRequestError(
                f'Histogram granularity `{time_granularity}` is not supported for {category} columns.'
            )
        return time_granularity
    for candidate_time_granularity in supported_time_granularities:
        if temporal_bin_count(min_value, max_value, candidate_time_granularity) >= 10:
            return candidate_time_granularity
    return supported_time_granularities[-1]


def fallback_histogram_time_granularity(time_granularity: str, category: str) -> str:
    if time_granularity != 'auto':
        return time_granularity
    return supported_histogram_time_granularities(category)[-1]


def supported_histogram_time_granularities(category: str) -> tuple[str, ...]:
    if category == 'date':
        return ('year', 'month', 'week', 'day')
    return TEMPORAL_TIME_GRANULARITIES


def temporal_bin_count(min_value: date | datetime, max_value: date | datetime, time_granularity: str) -> int:
    count = 0
    cursor = floor_temporal_value(min_value, time_granularity)
    final_end = advance_temporal_value(floor_temporal_value(max_value, time_granularity), time_granularity)
    while cursor < final_end:
        count += 1
        cursor = advance_temporal_value(cursor, time_granularity)
    return count


def floor_temporal_value(value: date | datetime, time_granularity: str) -> date | datetime:
    if isinstance(value, datetime):
        if time_granularity == 'year':
            return value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if time_granularity == 'month':
            return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if time_granularity == 'week':
            return (value - timedelta(days=value.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        if time_granularity == 'day':
            return value.replace(hour=0, minute=0, second=0, microsecond=0)
        if time_granularity == 'hour':
            return value.replace(minute=0, second=0, microsecond=0)
    elif isinstance(value, date):
        if time_granularity == 'year':
            return value.replace(month=1, day=1)
        if time_granularity == 'month':
            return value.replace(day=1)
        if time_granularity == 'week':
            return value - timedelta(days=value.weekday())
        if time_granularity == 'day':
            return value
    raise InvalidRequestError(f'Unsupported histogram granularity `{time_granularity}`.')


def advance_temporal_value(value: date | datetime, time_granularity: str) -> date | datetime:
    if time_granularity == 'year':
        return value.replace(year=value.year + 1, month=1, day=1)
    if time_granularity == 'month':
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1, day=1)
        return value.replace(month=value.month + 1, day=1)
    if time_granularity == 'week':
        return value + timedelta(days=7)
    if time_granularity == 'day':
        return value + timedelta(days=1)
    if time_granularity == 'hour' and isinstance(value, datetime):
        return value + timedelta(hours=1)
    raise InvalidRequestError(f'Unsupported histogram granularity `{time_granularity}`.')


def polars_granularity_every(time_granularity: str) -> str:
    mapping = {
        'year': '1y',
        'month': '1mo',
        'week': '1w',
        'day': '1d',
        'hour': '1h',
    }
    try:
        return mapping[time_granularity]
    except KeyError as exc:
        raise InvalidRequestError(f'Unsupported histogram granularity `{time_granularity}`.') from exc


def coerce_temporal_selection_value(value: object, *, column: str, dtype: pl.DataType) -> date | datetime:
    dtype_time_category = dtype_category(dtype)
    if dtype_time_category == 'date':
        if isinstance(value, int | float) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, tz=UTC).date()
        if isinstance(value, str) and value.strip():
            try:
                return date.fromisoformat(value.strip())
            except ValueError as exc:
                raise InvalidRequestError(
                    f'Filter `selection_range` for column `{column}` expects ISO date strings or epoch milliseconds.'
                ) from exc
    if dtype_time_category == 'datetime':
        if isinstance(value, int | float) and not isinstance(value, bool):
            as_utc = datetime.fromtimestamp(value / 1000, tz=UTC)
            return as_utc if getattr(dtype, 'time_zone', None) else as_utc.replace(tzinfo=None)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().replace('Z', '+00:00')
            try:
                return datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise InvalidRequestError(
                    f'Filter `selection_range` for column `{column}` expects '
                    'ISO datetime strings or epoch milliseconds.'
                ) from exc
    raise InvalidRequestError(f'Filter `selection_range` for column `{column}` expects temporal values.')


def temporal_value_to_epoch_ms(value: date | datetime) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            timestamp = value.astimezone(UTC).timestamp()
        else:
            epoch = datetime(1970, 1, 1)
            timestamp = (value - epoch).total_seconds()
        return round(timestamp * 1000)
    epoch_date = date(1970, 1, 1)
    return (value - epoch_date).days * 24 * 60 * 60 * 1000


def format_histogram_bin_label(start: date | datetime, end: date | datetime, time_granularity: str) -> str:
    if time_granularity == 'hour' and isinstance(start, datetime) and isinstance(end, datetime):
        return f'{start.strftime("%H:%M")} to {end.strftime("%H:%M")}'
    if time_granularity == 'day':
        return format_temporal_date_label(start)
    inclusive_end = end - timedelta(days=1)
    start_label = format_temporal_date_label(start)
    end_label = format_temporal_date_label(inclusive_end)
    if start_label == end_label:
        return start_label
    return f'{start_label} to {end_label}'


def format_temporal_date_label(value: date | datetime) -> str:
    return value.strftime('%b %d, %Y').replace(' 0', ' ')


def _temporal_series_category(series: pd.Series) -> str | None:
    if pd.api.types.is_datetime64_any_dtype(series):
        return 'datetime'
    inferred = pd.api.types.infer_dtype(series, skipna=True)
    if inferred == 'date':
        return 'date'
    if inferred in {'datetime', 'datetime64'}:
        return 'datetime'
    return None
