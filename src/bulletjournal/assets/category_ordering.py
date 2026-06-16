from __future__ import annotations

import json
import math
from typing import Any

import polars as pl

from bulletjournal.assets.prepare_utils import coerce_filter_value, json_safe_value
from bulletjournal.assets.serialization import json_safe_modifier_value
from bulletjournal.domain.errors import InvalidRequestError

CATEGORY_ORDER_MODES = {
    'category_asc',
    'category_desc',
    'value_asc',
    'value_desc',
}


def normalize_category_order_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in CATEGORY_ORDER_MODES else None


def validate_category_order_value(value: object, *, label: str) -> None:
    if normalize_category_order_mode(value) is not None:
        return
    if isinstance(value, list):
        for entry in value:
            normalized = json_safe_modifier_value(entry)
            if normalized is None:
                raise TypeError(f'{label} list entries cannot be null.')
            if isinstance(normalized, float) and not math.isfinite(normalized):
                raise TypeError(f'{label} list entries must be finite values.')
            if not isinstance(normalized, str | int | float | bool):
                raise TypeError(
                    f'{label} must be one of: {", ".join(sorted(CATEGORY_ORDER_MODES))}, or a list of category values.'
                )
        return
    raise TypeError(f'{label} must be one of: {", ".join(sorted(CATEGORY_ORDER_MODES))}, or a list of category values.')


def normalize_category_order_modifier_value(value: object, *, label: str) -> str | list[Any]:
    mode = normalize_category_order_mode(value)
    if mode is not None:
        return mode
    if not isinstance(value, list):
        raise TypeError(
            f'{label} must be one of: {", ".join(sorted(CATEGORY_ORDER_MODES))}, or a list of category values.'
        )
    normalized_values: list[Any] = []
    seen_keys: set[str] = set()
    for entry in value:
        normalized = json_safe_modifier_value(entry)
        if normalized is None:
            raise TypeError(f'{label} list entries cannot be null.')
        if isinstance(normalized, float) and not math.isfinite(normalized):
            raise TypeError(f'{label} list entries must be finite values.')
        if not isinstance(normalized, str | int | float | bool):
            raise TypeError(
                f'{label} must be one of: {", ".join(sorted(CATEGORY_ORDER_MODES))}, or a list of category values.'
            )
        value_key = category_order_value_key(normalized)
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        normalized_values.append(normalized)
    return normalized_values


def resolve_category_order(
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    *,
    default_mode: str,
    column: str,
    dtype: pl.DataType,
) -> str | list[Any]:
    candidate = (
        default_modifiers.get('category_order', default_mode) if isinstance(default_modifiers, dict) else default_mode
    )
    if 'category_order' in modifier_overrides:
        candidate = modifier_overrides['category_order']
    mode = normalize_category_order_mode(candidate)
    if mode is not None:
        return mode
    if not isinstance(candidate, list):
        allowed = ', '.join(sorted(CATEGORY_ORDER_MODES))
        raise InvalidRequestError(f'category_order must be one of: {allowed}, or an array of category values.')
    resolved: list[Any] = []
    seen_keys: set[str] = set()
    for value in candidate:
        coerced = coerce_filter_value(dtype, value, column=column, kind='category_order')
        if coerced is None:
            raise InvalidRequestError('category_order cannot contain null values.')
        value_key = category_order_value_key(coerced)
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        resolved.append(coerced)
    return resolved


def sort_category_rows(
    rows: list[dict[str, Any]],
    *,
    category_field: str,
    value_field: str,
    default_mode: str,
    category_order: str | list[Any],
) -> list[dict[str, Any]]:
    if isinstance(category_order, str):
        return sort_category_rows_by_mode(
            rows, category_field=category_field, value_field=value_field, mode=category_order
        )
    fallback_rows = sort_category_rows_by_mode(
        rows, category_field=category_field, value_field=value_field, mode=default_mode
    )
    if not category_order:
        return fallback_rows
    rows_by_key = {category_order_value_key(row.get(category_field)): row for row in rows}
    ordered_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for value in category_order:
        value_key = category_order_value_key(value)
        row = rows_by_key.get(value_key)
        if row is None or value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        ordered_rows.append(row)
    for row in fallback_rows:
        value_key = category_order_value_key(row.get(category_field))
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        ordered_rows.append(row)
    return ordered_rows


def sort_category_rows_by_mode(
    rows: list[dict[str, Any]], *, category_field: str, value_field: str, mode: str
) -> list[dict[str, Any]]:
    if mode == 'category_asc':
        return sorted(rows, key=lambda row: sortable_category_value(row.get(category_field)))
    if mode == 'category_desc':
        return sorted(rows, key=lambda row: sortable_category_value(row.get(category_field)), reverse=True)
    category_sorted_rows = sorted(rows, key=lambda row: sortable_category_value(row.get(category_field)))
    return sorted(category_sorted_rows, key=lambda row: row.get(value_field), reverse=mode == 'value_desc')


def sortable_category_value(value: Any) -> Any:
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return normalized
    return value


def category_order_value_key(value: object) -> str:
    return json.dumps(json_safe_value(value), ensure_ascii=True, sort_keys=True)
