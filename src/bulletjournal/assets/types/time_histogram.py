from __future__ import annotations

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

DEFAULT_TIME_HISTOGRAM_GRANULARITY = 'auto'
TIME_HISTOGRAM_GRANULARITIES = ('auto', 'year', 'month', 'week', 'day', 'hour')
TEMPORAL_GRANULARITIES = ('year', 'month', 'week', 'day', 'hour')


@dataclass(slots=True, init=False)
class TimeHistogram(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    granularity: str
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'time_histogram'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        x,
        granularity: str = DEFAULT_TIME_HISTOGRAM_GRANULARITY,
        **modifier_kwargs: Any,
    ) -> None:
        if 'bins' in modifier_kwargs:
            raise TypeError('TimeHistogram assets do not support a `bins` argument. Use `granularity` instead.')
        unsupported_encodings = sorted(set(modifier_kwargs) & {'shape', 'size', 'color'})
        if unsupported_encodings:
            joined = ', '.join(f'`{key}`' for key in unsupported_encodings)
            raise TypeError(f'TimeHistogram assets do not support {joined} arguments.')
        self.dataframe = dataframe
        self.x = x
        self.granularity = granularity
        self.modifier_defaults = modifier_kwargs or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('TimeHistogram assets require a pandas.DataFrame payload.')
        if not isinstance(self.x, str) or not self.x:
            raise TypeError('TimeHistogram assets require `x` to be a non-empty column name.')
        if self.x not in self.dataframe.columns:
            raise ValueError(f'TimeHistogram column `{self.x}` was not found in the provided DataFrame.')
        series = self.dataframe[self.x]
        category = _temporal_series_category(series)
        if category is None:
            raise TypeError(f'TimeHistogram column `{self.x}` must use a date or datetime dtype.')
        if self.granularity not in TIME_HISTOGRAM_GRANULARITIES:
            allowed = ', '.join(TIME_HISTOGRAM_GRANULARITIES)
            raise TypeError(f'TimeHistogram `granularity` must be one of: {allowed}.')
        if self.granularity != 'auto' and self.granularity not in supported_time_histogram_granularities(category):
            raise TypeError(f'TimeHistogram granularity `{self.granularity}` is not supported for {category} columns.')
        validate_time_histogram_modifier_defaults(self.modifier_defaults)


def validate_time_histogram_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={'bar_width', 'border_thickness', 'x_axis', 'y_axis', 'title'},
        context='TimeHistogram assets',
    )
    if value is None:
        return
    if 'bar_width' in value:
        validate_number(value['bar_width'], label='TimeHistogram modifier `bar_width`')
    if 'border_thickness' in value:
        validate_number(value['border_thickness'], label='TimeHistogram modifier `border_thickness`')
    if 'x_axis' in value:
        validate_axis_modifier_defaults(value['x_axis'], label='TimeHistogram modifier `x_axis`')
    if 'y_axis' in value:
        validate_axis_modifier_defaults(value['y_axis'], label='TimeHistogram modifier `y_axis`')
    if 'title' in value:
        validate_title_modifier_defaults(value['title'], label='TimeHistogram modifier `title`')


def time_histogram_chart_modifier_defaults(
    *, title: str, x_column: str, y_axis_label: str, granularity: str
) -> dict[str, Any]:
    return {
        'granularity': granularity,
        'bar_width': 90,
        'border_thickness': 0,
        'x_axis': {**axis_modifier_defaults(x_column), 'tick_count': 20},
        'y_axis': axis_modifier_defaults(y_axis_label),
        'title': title_modifier_defaults(title),
    }


def time_histogram_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'id': 'granularity',
            'title': 'Granularity',
            'kind': 'enum',
            'category': 'saved_view',
            'server_targets': ['main'],
            'default_value': default_modifiers['granularity'],
            'options': list(TIME_HISTOGRAM_GRANULARITIES),
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


def serialize_time_histogram(
    asset: TimeHistogram,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    default_modifiers = {
        'page': {'index': 0, 'size': 10},
        'sort': [],
        'filters': [],
        **merge_nested_dicts(
            time_histogram_chart_modifier_defaults(
                title=title,
                x_column=str(asset.x),
                y_axis_label='Rows',
                granularity=asset.granularity,
            ),
            asset.modifier_defaults,
        ),
    }
    modifier_schema = [
        *dataset_modifier_schema(column_definitions, default_modifiers, filters_targets=['main', 'table']),
        *time_histogram_chart_modifier_schema(default_modifiers),
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


def prepare_time_histogram(
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
        raise InvalidRequestError('TimeHistogram asset definition is missing its source column.')
    histogram_dtype = schema[column_id_map[histogram_column]]
    histogram_category = dtype_category(histogram_dtype)
    if histogram_category not in {'date', 'datetime'}:
        raise InvalidRequestError(f'TimeHistogram column `{histogram_column}` must use a date or datetime dtype.')
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_granularity = resolve_time_histogram_granularity(default_modifiers, modifier_overrides)
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selection_ranges = resolve_time_histogram_selection_ranges(
        transient_modifiers,
        column=histogram_column,
        dtype=histogram_dtype,
    )
    table_frame = filtered_frame
    if selection_ranges:
        table_frame = apply_time_histogram_selections(table_frame, histogram_column, selection_ranges, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_time_histogram_main_payload(
            filtered_frame,
            column=histogram_column,
            column_id_map=column_id_map,
            granularity=resolved_granularity,
            histogram_category=histogram_category,
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
        'granularity': resolved_granularity,
    }


def prepare_time_histogram_main_payload(
    frame: pl.LazyFrame,
    *,
    column: str,
    column_id_map: dict[str, Any],
    granularity: str,
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
            'time_granularity': fallback_time_histogram_granularity(granularity, histogram_category),
        }
    min_value = stats['min_value'][0]
    max_value = stats['max_value'][0]
    actual_granularity = effective_time_histogram_granularity(
        granularity, min_value=min_value, max_value=max_value, category=histogram_category
    )
    bin_start = floor_temporal_value(min_value, actual_granularity)
    max_bin_start = floor_temporal_value(max_value, actual_granularity)
    final_end = advance_temporal_value(max_bin_start, actual_granularity)
    grouped = (
        frame.filter(pl.col(column_name).is_not_null())
        .with_columns(
            pl.col(column_name).dt.truncate(polars_granularity_every(actual_granularity)).alias('__histogram_bin_start')
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
        next_cursor = advance_temporal_value(cursor, actual_granularity)
        bins.append(
            {
                'index': index,
                'start': temporal_value_to_epoch_ms(cursor),
                'end': temporal_value_to_epoch_ms(next_cursor),
                'count': counts_by_start.get(cursor, 0),
                'label': format_time_histogram_bin_label(cursor, next_cursor, actual_granularity),
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
        'time_granularity': actual_granularity,
    }


def resolve_time_histogram_granularity(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> str:
    candidate = (
        default_modifiers.get('granularity', DEFAULT_TIME_HISTOGRAM_GRANULARITY)
        if isinstance(default_modifiers, dict)
        else DEFAULT_TIME_HISTOGRAM_GRANULARITY
    )
    if 'granularity' in modifier_overrides:
        candidate = modifier_overrides['granularity']
    return coerce_time_histogram_granularity(candidate)


def resolve_time_histogram_selection_ranges(
    transient_modifiers: dict[str, Any],
    *,
    column: str,
    dtype: pl.DataType,
) -> list[dict[str, date | datetime]]:
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
    resolved_ranges: list[dict[str, date | datetime]] = []
    for entry in raw_ranges:
        if not isinstance(entry, dict):
            raise InvalidRequestError('transient_modifiers.selection_ranges entries must be objects.')
        lower = coerce_time_histogram_selection_value(entry.get('lower'), column=column, dtype=dtype)
        upper = coerce_time_histogram_selection_value(entry.get('upper'), column=column, dtype=dtype)
        if lower > upper:
            lower, upper = upper, lower
        resolved_ranges.append({'lower': lower, 'upper': upper})
    return resolved_ranges


def apply_time_histogram_selections(
    frame: pl.LazyFrame,
    column: str,
    selection_ranges: list[dict[str, date | datetime]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    predicate = pl.lit(False)
    for selection_range in selection_ranges:
        predicate = predicate | (
            (pl.col(column_name) >= selection_range['lower']) & (pl.col(column_name) < selection_range['upper'])
        )
    return frame.filter(pl.col(column_name).is_not_null() & predicate)


def coerce_time_histogram_granularity(value: object) -> str:
    if not isinstance(value, str) or value not in TIME_HISTOGRAM_GRANULARITIES:
        allowed = ', '.join(TIME_HISTOGRAM_GRANULARITIES)
        raise InvalidRequestError(f'TimeHistogram granularity must be one of: {allowed}.')
    return value


def effective_time_histogram_granularity(
    granularity: str, *, min_value: date | datetime, max_value: date | datetime, category: str
) -> str:
    supported = supported_time_histogram_granularities(category)
    if granularity != 'auto':
        if granularity not in supported:
            raise InvalidRequestError(
                f'TimeHistogram granularity `{granularity}` is not supported for {category} columns.'
            )
        return granularity
    for candidate in supported:
        if temporal_bin_count(min_value, max_value, candidate) >= 10:
            return candidate
    return supported[-1]


def fallback_time_histogram_granularity(granularity: str, category: str) -> str:
    if granularity != 'auto':
        return granularity
    return supported_time_histogram_granularities(category)[-1]


def supported_time_histogram_granularities(category: str) -> tuple[str, ...]:
    if category == 'date':
        return ('year', 'month', 'week', 'day')
    return TEMPORAL_GRANULARITIES


def temporal_bin_count(min_value: date | datetime, max_value: date | datetime, granularity: str) -> int:
    count = 0
    cursor = floor_temporal_value(min_value, granularity)
    final_end = advance_temporal_value(floor_temporal_value(max_value, granularity), granularity)
    while cursor < final_end:
        count += 1
        cursor = advance_temporal_value(cursor, granularity)
    return count


def floor_temporal_value(value: date | datetime, granularity: str) -> date | datetime:
    if isinstance(value, datetime):
        if granularity == 'year':
            return value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if granularity == 'month':
            return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if granularity == 'week':
            return (value - timedelta(days=value.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        if granularity == 'day':
            return value.replace(hour=0, minute=0, second=0, microsecond=0)
        if granularity == 'hour':
            return value.replace(minute=0, second=0, microsecond=0)
    elif isinstance(value, date):
        if granularity == 'year':
            return value.replace(month=1, day=1)
        if granularity == 'month':
            return value.replace(day=1)
        if granularity == 'week':
            return value - timedelta(days=value.weekday())
        if granularity == 'day':
            return value
    raise InvalidRequestError(f'Unsupported TimeHistogram granularity `{granularity}`.')


def advance_temporal_value(value: date | datetime, granularity: str) -> date | datetime:
    if granularity == 'year':
        return value.replace(year=value.year + 1, month=1, day=1)
    if granularity == 'month':
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1, day=1)
        return value.replace(month=value.month + 1, day=1)
    if granularity == 'week':
        return value + timedelta(days=7)
    if granularity == 'day':
        return value + timedelta(days=1)
    if granularity == 'hour' and isinstance(value, datetime):
        return value + timedelta(hours=1)
    raise InvalidRequestError(f'Unsupported TimeHistogram granularity `{granularity}`.')


def polars_granularity_every(granularity: str) -> str:
    mapping = {
        'year': '1y',
        'month': '1mo',
        'week': '1w',
        'day': '1d',
        'hour': '1h',
    }
    try:
        return mapping[granularity]
    except KeyError as exc:
        raise InvalidRequestError(f'Unsupported TimeHistogram granularity `{granularity}`.') from exc


def coerce_time_histogram_selection_value(value: object, *, column: str, dtype: pl.DataType) -> date | datetime:
    category = dtype_category(dtype)
    if category == 'date':
        if isinstance(value, int | float) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, tz=UTC).date()
        if isinstance(value, str) and value.strip():
            try:
                return date.fromisoformat(value.strip())
            except ValueError as exc:
                raise InvalidRequestError(
                    f'Filter `selection_range` for column `{column}` expects ISO date strings or epoch milliseconds.'
                ) from exc
    if category == 'datetime':
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


def format_time_histogram_bin_label(start: date | datetime, end: date | datetime, granularity: str) -> str:
    if granularity == 'hour' and isinstance(start, datetime) and isinstance(end, datetime):
        return f'{start.strftime("%H:%M")} to {end.strftime("%H:%M")}'
    if granularity == 'day':
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
