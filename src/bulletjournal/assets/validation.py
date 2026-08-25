from __future__ import annotations

import re
from typing import Any

import pandas as pd

HIGHLIGHT_COLOR_PATTERN = re.compile(r'^#[0-9a-fA-F]{6}$')


def validate_highlights(value: object, *, dataframe: pd.DataFrame, label: str = 'Highlights') -> None:
    if not isinstance(value, list):
        raise TypeError(f'{label} must be a list.')
    for entry in value:
        if not isinstance(entry, dict):
            raise TypeError(f'{label} entries must be dicts.')
        column = entry.get('column')
        if not isinstance(column, str) or column not in dataframe.columns:
            raise ValueError(f'{label} entry references unknown column `{column}`.')
        if entry.get('kind') not in {'range', 'value', 'regex'}:
            raise TypeError(f'{label} entries require kind `range`, `value`, or `regex`.')
        if entry.get('highlight_scope', 'cell') not in {'cell', 'row'}:
            raise TypeError(f'{label} entry scope must be `cell` or `row`.')
        color = entry.get('highlight_color')
        if not isinstance(color, str) or not HIGHLIGHT_COLOR_PATTERN.fullmatch(color):
            raise TypeError(f'{label} entry color must be a six-digit hex color.')


def validate_optional_asset_column(dataframe: pd.DataFrame, column: str | None, *, label: str) -> None:
    if column is None:
        return
    if not isinstance(column, str) or not column:
        raise TypeError(f'{label} must be a non-empty column name when provided.')
    if column not in dataframe.columns:
        raise ValueError(f'{label} column `{column}` was not found in the provided DataFrame.')


def validate_modifier_defaults(value: dict[str, object] | None, *, allowed_keys: set[str], context: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise TypeError(f'{context} modifier defaults must be provided as a dict when set.')
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{context} received unsupported modifier defaults: {joined}.')


def validate_axis_modifier_defaults(value: object, *, label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f'{label} must be a dict.')
    allowed_keys = {'label_size', 'label', 'hide_label', 'tick_count', 'tick_size', 'show_grid_lines', 'scale'}
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{label} received unsupported keys: {joined}.')
    if 'label_size' in value:
        validate_number_or_none(value['label_size'], label=f'{label}.label_size')
    if 'label' in value and not isinstance(value['label'], str):
        raise TypeError(f'{label}.label must be a string.')
    if 'hide_label' in value and not isinstance(value['hide_label'], bool):
        raise TypeError(f'{label}.hide_label must be a bool.')
    if 'tick_count' in value:
        validate_int_or_none(value['tick_count'], label=f'{label}.tick_count')
    if 'tick_size' in value:
        validate_number_or_none(value['tick_size'], label=f'{label}.tick_size')
    if 'show_grid_lines' in value and not isinstance(value['show_grid_lines'], bool):
        raise TypeError(f'{label}.show_grid_lines must be a bool.')
    if 'scale' in value and value['scale'] not in {'lin', 'log'}:
        raise TypeError(f'{label}.scale must be `lin` or `log`.')


def validate_title_modifier_defaults(value: object, *, label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f'{label} must be a dict.')
    allowed_keys = {'size', 'text', 'hide_title', 'position'}
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{label} received unsupported keys: {joined}.')
    if 'size' in value:
        validate_number_or_none(value['size'], label=f'{label}.size')
    if 'text' in value and not isinstance(value['text'], str):
        raise TypeError(f'{label}.text must be a string.')
    if 'hide_title' in value and not isinstance(value['hide_title'], bool):
        raise TypeError(f'{label}.hide_title must be a bool.')
    if 'position' in value and value['position'] not in {'top', 'bottom'}:
        raise TypeError(f'{label}.position must be `top` or `bottom`.')


def validate_positive_int(value: object, *, label: str) -> None:
    if not isinstance(value, int) or value < 1:
        raise TypeError(f'{label} must be a positive integer.')


def validate_int_or_none(value: object, *, label: str) -> None:
    if value is not None and not isinstance(value, int):
        raise TypeError(f'{label} must be an integer or None.')


def validate_number(value: object, *, label: str) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f'{label} must be a number.')


def validate_number_or_none(value: object, *, label: str) -> None:
    if value is not None:
        validate_number(value, label=label)


def merge_nested_dicts(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        keys = set(base) | set(override)
        return {key: merge_nested_dicts(base.get(key), override.get(key)) for key in keys}
    return override if override is not None else base
