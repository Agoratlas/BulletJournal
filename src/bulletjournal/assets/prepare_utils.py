from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import polars as pl

from bulletjournal.domain.errors import InvalidRequestError

ALLOWED_PAGE_SIZES = {10, 25, 50, 100}
DEFAULT_PAGE_SIZE = 25


def backing_dataset_object(objects: object) -> dict[str, Any]:
    if not isinstance(objects, list):
        raise InvalidRequestError('Asset backing dataset metadata is missing.')
    for item in objects:
        if isinstance(item, dict) and item.get('object_role') == 'backing_dataset' and item.get('artifact_hash'):
            return item
    raise InvalidRequestError('Asset backing dataset is missing.')


def prepared_table_payload(
    frame: pl.LazyFrame,
    *,
    schema: pl.Schema,
    column_names: list[str],
    resolved_page: dict[str, int],
    resolved_sort: list[dict[str, str]],
) -> dict[str, Any]:
    rows_total_frame = frame.select(pl.len().alias('rows_total')).collect()
    rows_total = int(rows_total_frame['rows_total'][0]) if rows_total_frame.height else 0
    page_index = resolved_page['index']
    page_size = resolved_page['size']
    rows = frame.slice(page_index * page_size, page_size).collect().to_dicts()
    return {
        'kind': 'table',
        'rows_total': rows_total,
        'columns': [
            {
                'id': str(name),
                'title': str(name),
                'data_type': str(schema[name]),
                'sortable': True,
                'filter_kinds': column_filter_kinds(schema[name]),
            }
            for name in column_names
        ],
        'page': resolved_page,
        'sort': resolved_sort,
        'rows': [{str(key): json_safe_value(value) for key, value in row.items()} for row in rows],
    }


def frame_with_filters(
    frame: pl.LazyFrame,
    resolved_filters: list[dict[str, Any]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    for filter_entry in resolved_filters:
        frame = apply_filter(frame, filter_entry, column_id_map)
    return frame


def frame_with_sort(
    frame: pl.LazyFrame,
    resolved_sort: list[dict[str, str]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    if not resolved_sort:
        return frame
    sort_entry = resolved_sort[0]
    return frame.sort(
        column_id_map[sort_entry['column']],
        descending=sort_entry['direction'] == 'desc',
        nulls_last=True,
    )


def resolve_page(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> dict[str, int]:
    default_page = default_modifiers.get('page') if isinstance(default_modifiers, dict) else None
    page_index = coerce_page_index(default_page.get('index') if isinstance(default_page, dict) else 0)
    page_size = coerce_page_size(default_page.get('size') if isinstance(default_page, dict) else DEFAULT_PAGE_SIZE)
    if 'page' in modifier_overrides:
        page_override = modifier_overrides['page']
        if not isinstance(page_override, dict):
            raise InvalidRequestError('modifier_overrides.page must be an object.')
        page_index = coerce_page_index(page_override.get('index', page_index))
        page_size = coerce_page_size(page_override.get('size', page_size))
    return {'index': page_index, 'size': page_size}


def resolve_sort(
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


def resolve_filters(
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    column_id_map: dict[str, Any],
    schema: pl.Schema,
) -> list[dict[str, Any]]:
    candidate = default_modifiers.get('filters') if isinstance(default_modifiers, dict) else []
    if 'filters' in modifier_overrides:
        candidate = modifier_overrides['filters']
    if candidate in (None, []):
        return []
    if not isinstance(candidate, list):
        raise InvalidRequestError('modifier_overrides.filters must be an array.')
    resolved: list[dict[str, Any]] = []
    seen_columns: set[str] = set()
    for entry in candidate:
        if not isinstance(entry, dict):
            raise InvalidRequestError('modifier_overrides.filters entries must be objects.')
        column = entry.get('column')
        if not isinstance(column, str) or column not in column_id_map:
            raise InvalidRequestError(f'Unknown filter column `{column}`.')
        if column in seen_columns:
            raise InvalidRequestError(f'Only one active filter per column is supported for `{column}`.')
        kind = entry.get('kind')
        if kind not in {'range', 'value', 'regex'}:
            raise InvalidRequestError('Filter kind must be `range`, `value`, or `regex`.')
        dtype = schema[column_id_map[column]]
        resolved.append(resolve_filter_entry(column=column, kind=str(kind), dtype=dtype, entry=entry))
        seen_columns.add(column)
    return resolved


def resolve_filter_entry(
    *,
    column: str,
    kind: str,
    dtype: pl.DataType,
    entry: dict[str, Any],
) -> dict[str, Any]:
    category = dtype_category(dtype)
    if kind == 'range':
        if category not in {'numeric', 'date', 'datetime', 'time'}:
            raise InvalidRequestError(f'Range filters are not supported for column `{column}`.')
        lower = coerce_filter_value(dtype, entry.get('lower'), column=column, kind='range')
        upper = coerce_filter_value(dtype, entry.get('upper'), column=column, kind='range')
        if lower is None and upper is None:
            raise InvalidRequestError(f'Range filter `{column}` must define `lower`, `upper`, or both.')
        return {
            'kind': 'range',
            'column': column,
            'value_type': category,
            'lower': json_safe_value(lower),
            'upper': json_safe_value(upper),
        }
    if kind == 'value':
        raw_values = entry.get('values')
        if raw_values is None:
            raw_values = []
        if not isinstance(raw_values, list):
            raise InvalidRequestError(f'Value filter `{column}` must define `values` as an array.')
        include_null = bool(entry.get('include_null', False))
        resolved_values = [
            coerce_filter_value(dtype, item, column=column, kind='value') for item in raw_values if item is not None
        ]
        if not resolved_values and not include_null:
            raise InvalidRequestError(f'Value filter `{column}` must define at least one value or include nulls.')
        return {
            'kind': 'value',
            'column': column,
            'value_type': category,
            'values': [json_safe_value(item) for item in resolved_values],
            'include_null': include_null,
        }
    if category != 'text':
        raise InvalidRequestError(f'Regex filters are only supported for string-like columns such as `{column}`.')
    pattern = entry.get('pattern')
    if not isinstance(pattern, str) or not pattern:
        raise InvalidRequestError(f'Regex filter `{column}` must define a non-empty `pattern`.')
    case_sensitive = bool(entry.get('case_sensitive', False))
    compiled_pattern = pattern if case_sensitive else f'(?i){pattern}'
    try:
        re.compile(compiled_pattern)
    except re.error as exc:
        raise InvalidRequestError(f'Invalid regex for column `{column}`: {exc}.') from exc
    return {
        'kind': 'regex',
        'column': column,
        'pattern': pattern,
        'case_sensitive': case_sensitive,
    }


def apply_filter(frame: pl.LazyFrame, filter_entry: dict[str, Any], column_id_map: dict[str, Any]) -> pl.LazyFrame:
    column_name = column_id_map[filter_entry['column']]
    expression = pl.col(column_name)
    kind = filter_entry['kind']
    if kind == 'range':
        value_type = str(filter_entry.get('value_type') or '')
        lower = restore_filter_value(filter_entry.get('lower'), value_type=value_type)
        upper = restore_filter_value(filter_entry.get('upper'), value_type=value_type)
        if lower is not None:
            frame = frame.filter(expression >= lower)
        if upper is not None:
            frame = frame.filter(expression <= upper)
        return frame
    if kind == 'value':
        value_type = str(filter_entry.get('value_type') or '')
        values = [restore_filter_value(item, value_type=value_type) for item in filter_entry.get('values', [])]
        predicate = expression.is_in(values).fill_null(False) if values else pl.lit(False)
        if bool(filter_entry.get('include_null', False)):
            predicate = predicate | expression.is_null()
        return frame.filter(predicate)
    pattern = str(filter_entry['pattern'])
    if not bool(filter_entry.get('case_sensitive', False)):
        pattern = f'(?i){pattern}'
    return frame.filter(expression.cast(pl.Utf8).str.contains(pattern).fill_null(False))


def coerce_page_index(value: object) -> int:
    if not isinstance(value, int) or value < 0:
        raise InvalidRequestError('Page index must be a zero-based integer.')
    return value


def coerce_page_size(value: object) -> int:
    if not isinstance(value, int) or value not in ALLOWED_PAGE_SIZES:
        allowed = ', '.join(str(size) for size in sorted(ALLOWED_PAGE_SIZES))
        raise InvalidRequestError(f'Page size must be one of: {allowed}.')
    return value


def column_filter_kinds(dtype: pl.DataType) -> list[str]:
    category = dtype_category(dtype)
    if category in {'numeric', 'date', 'datetime', 'time'}:
        return ['range', 'value']
    if category == 'bool':
        return ['value']
    return ['value', 'regex']


def dtype_category(dtype: pl.DataType) -> str:
    label = str(dtype)
    if label.startswith(('Int', 'UInt', 'Float', 'Decimal')):
        return 'numeric'
    if label == 'Date':
        return 'date'
    if label.startswith('Datetime'):
        return 'datetime'
    if label == 'Time':
        return 'time'
    if label == 'Boolean':
        return 'bool'
    return 'text'


def coerce_filter_value(dtype: pl.DataType, value: object, *, column: str, kind: str) -> Any:
    if value is None:
        return None
    category = dtype_category(dtype)
    if category == 'numeric':
        return coerce_numeric_filter_value(value, column=column, kind=kind)
    if category == 'date':
        return coerce_date_filter_value(value, column=column, kind=kind)
    if category == 'datetime':
        return coerce_datetime_filter_value(value, column=column, kind=kind)
    if category == 'time':
        return coerce_time_filter_value(value, column=column, kind=kind)
    if category == 'bool':
        return coerce_bool_filter_value(value, column=column, kind=kind)
    if isinstance(value, str):
        return value
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects string values.')


def coerce_numeric_filter_value(value: object, *, column: str, kind: str) -> int | float:
    if isinstance(value, bool):
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects numeric values.')
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects finite numeric values.')
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects finite numeric values.')
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = Decimal(value.strip())
        except Exception as exc:
            raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects numeric values.') from exc
        if not parsed.is_finite():
            raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects finite numeric values.')
        return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects numeric values.')


def coerce_date_filter_value(value: object, *, column: str, kind: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO date strings.')
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO date strings.') from exc


def coerce_datetime_filter_value(value: object, *, column: str, kind: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO datetime strings.')
    normalized = value.strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO datetime strings.') from exc


def coerce_time_filter_value(value: object, *, column: str, kind: str) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO time strings.')
    try:
        return time.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO time strings.') from exc


def coerce_bool_filter_value(value: object, *, column: str, kind: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == 'true':
            return True
        if normalized == 'false':
            return False
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects boolean values.')


def restore_filter_value(value: object, *, value_type: str) -> Any:
    if value is None or value_type in {'numeric', 'bool', ''} or not isinstance(value, str):
        return value
    parsers = {
        'date': date.fromisoformat,
        'datetime': datetime.fromisoformat,
        'time': time.fromisoformat,
    }
    parser = parsers.get(value_type)
    if parser is None:
        return value
    normalized = value.replace('Z', '+00:00') if value_type == 'datetime' else value
    try:
        return parser(normalized)
    except ValueError:
        try:
            return parser(value)
        except ValueError:
            return value


def numeric_plot_domain(
    *, min_value: float, max_value: float, column: str, context: str
) -> dict[str, int | float | None]:
    if not math.isfinite(min_value) or not math.isfinite(max_value):
        raise InvalidRequestError(f'{context} `{column}` contains non-finite numeric values.')
    if min_value == max_value:
        return {'min': json_safe_value(min_value - 0.5), 'max': json_safe_value(max_value + 0.5)}
    return {'min': json_safe_value(min_value), 'max': json_safe_value(max_value)}


def json_safe_value(value: Any) -> Any:
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
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe_value(item) for item in value]
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return json_safe_value(normalized)
    isoformat = getattr(value, 'isoformat', None)
    if callable(isoformat):
        try:
            return isoformat()
        except TypeError:
            pass
    return str(value)
