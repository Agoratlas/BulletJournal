from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.prepare_utils import (
    coerce_filter_value,
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
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

MAX_SCATTER_PLOT_POINTS = 10_000


@dataclass(slots=True, init=False)
class ScatterPlot(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    y: str
    label: str | None
    shape: str | None
    size: str | None
    color: str | None
    modifier_defaults: dict[str, object] | None

    asset_type_id = 'scatter_plot'
    interactive = True

    def __init__(
        self,
        dataframe,
        *,
        x,
        y,
        label=None,
        shape=None,
        size=None,
        color=None,
        size_scaling=1,
        **modifier_kwargs: Any,
    ) -> None:
        self.dataframe = dataframe
        self.x = x
        self.y = y
        self.label = label
        self.shape = shape
        self.size = size
        self.color = color
        modifier_defaults = dict(modifier_kwargs)
        if size_scaling != 1:
            modifier_defaults['size_scaling'] = size_scaling
        self.modifier_defaults = modifier_defaults or None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Scatter plot assets require a pandas.DataFrame payload.')
        if len(self.dataframe.index) > MAX_SCATTER_PLOT_POINTS:
            raise ValueError(
                f'Scatter plot assets support at most {MAX_SCATTER_PLOT_POINTS:,} rows; '
                f'received {len(self.dataframe.index):,}.'
            )
        if not isinstance(self.x, str) or not self.x:
            raise TypeError('Scatter plot assets require `x` to be a non-empty column name.')
        if not isinstance(self.y, str) or not self.y:
            raise TypeError('Scatter plot assets require `y` to be a non-empty column name.')
        if self.x not in self.dataframe.columns:
            raise ValueError(f'Scatter plot x column `{self.x}` was not found in the provided DataFrame.')
        if self.y not in self.dataframe.columns:
            raise ValueError(f'Scatter plot y column `{self.y}` was not found in the provided DataFrame.')
        if not pd.api.types.is_numeric_dtype(self.dataframe[self.x]):
            raise TypeError(f'Scatter plot x column `{self.x}` must use a numeric dtype.')
        if not pd.api.types.is_numeric_dtype(self.dataframe[self.y]):
            raise TypeError(f'Scatter plot y column `{self.y}` must use a numeric dtype.')
        validate_optional_asset_column(self.dataframe, self.label, label='Scatter plot `label`')
        validate_optional_asset_column(self.dataframe, self.shape, label='Scatter plot `shape`')
        validate_optional_asset_column(self.dataframe, self.size, label='Scatter plot `size`')
        validate_optional_asset_column(self.dataframe, self.color, label='Scatter plot `color`')
        validate_scatter_plot_modifier_defaults(self.modifier_defaults)


def validate_scatter_plot_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={
            'min_point_size',
            'max_point_size',
            'size_scaling',
            'show_legend',
            'shape_style',
            'x_axis',
            'y_axis',
            'title',
        },
        context='Scatter plot assets',
    )
    if value is None:
        return
    if 'min_point_size' in value:
        validate_number(value['min_point_size'], label='Scatter plot modifier `min_point_size`')
    if 'max_point_size' in value:
        validate_number(value['max_point_size'], label='Scatter plot modifier `max_point_size`')
    if 'size_scaling' in value:
        validate_number(value['size_scaling'], label='Scatter plot modifier `size_scaling`')
        if not 0.1 <= float(value['size_scaling']) <= 3.0:
            raise ValueError('Scatter plot modifier `size_scaling` must be between 0.1 and 3.0.')
    if 'show_legend' in value and not isinstance(value['show_legend'], bool):
        raise TypeError('Scatter plot modifier `show_legend` must be a bool.')
    if 'shape_style' in value and value['shape_style'] not in {'outline', 'filled'}:
        raise TypeError('Scatter plot modifier `shape_style` must be `outline` or `filled`.')
    if 'x_axis' in value:
        validate_axis_modifier_defaults(value['x_axis'], label='Scatter plot modifier `x_axis`')
    if 'y_axis' in value:
        validate_axis_modifier_defaults(value['y_axis'], label='Scatter plot modifier `y_axis`')
    if 'title' in value:
        validate_title_modifier_defaults(value['title'], label='Scatter plot modifier `title`')


def scatter_plot_modifier_defaults(*, title: str, x_column: str, y_column: str) -> dict[str, Any]:
    return {
        'min_point_size': 50,
        'max_point_size': 250,
        'size_scaling': 1,
        'show_legend': True,
        'shape_style': 'outline',
        'x_axis': axis_modifier_defaults(x_column),
        'y_axis': axis_modifier_defaults(y_column),
        'title': title_modifier_defaults(title),
    }


def scatter_plot_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'id': 'min_point_size',
            'title': 'Min point size',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['min_point_size'],
            'min_value': 0,
            'step': 1,
        },
        {
            'id': 'max_point_size',
            'title': 'Max point size',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['max_point_size'],
            'min_value': 0,
            'step': 1,
        },
        {
            'id': 'size_scaling',
            'title': 'Size scaling',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['size_scaling'],
            'min_value': 0.1,
            'max_value': 3.0,
            'step': 0.1,
        },
        {
            'id': 'show_legend',
            'title': 'Show legend',
            'kind': 'bool',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['show_legend'],
        },
        {
            'id': 'shape_style',
            'title': 'Shape style',
            'kind': 'enum',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['shape_style'],
            'options': ['outline', 'filled'],
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


def serialize_scatter_plot(
    asset: ScatterPlot,
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
            scatter_plot_modifier_defaults(title=title, x_column=str(asset.x), y_column=str(asset.y)),
            asset.modifier_defaults,
        ),
    }
    modifier_schema = [
        *dataset_modifier_schema(column_definitions, default_modifiers, filters_targets=['main', 'table']),
        *scatter_plot_modifier_schema(default_modifiers),
        {
            'id': 'selection_bounds',
            'title': 'Selected region',
            'kind': 'range_2d',
            'category': 'transient_view',
            'server_targets': ['table'],
            'default_value': None,
            'x_column': str(asset.x),
            'y_column': str(asset.y),
        },
        {
            'id': 'selected_row_index',
            'title': 'Selected point',
            'kind': 'int',
            'category': 'transient_view',
            'server_targets': ['table'],
            'default_value': None,
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
            'scatter_x_column': str(asset.x),
            'scatter_y_column': str(asset.y),
            'scatter_label_column': str(asset.label) if asset.label is not None else None,
            'scatter_shape_column': str(asset.shape) if asset.shape is not None else None,
            'scatter_size_column': str(asset.size) if asset.size is not None else None,
            'scatter_color_column': str(asset.color) if asset.color is not None else None,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def prepare_scatter_plot(
    *,
    dataset_path: Path,
    definition: dict[str, Any],
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    transient_modifiers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_frame = pl.scan_parquet(dataset_path)
    schema = base_frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    frame = base_frame.with_row_index('__scatter_row_index')
    x_column = definition.get('scatter_x_column')
    y_column = definition.get('scatter_y_column')
    label_column = resolve_optional_scatter_encoding_column(definition, 'scatter_label_column', column_id_map)
    shape_column = resolve_optional_scatter_encoding_column(definition, 'scatter_shape_column', column_id_map)
    size_column = resolve_optional_scatter_encoding_column(definition, 'scatter_size_column', column_id_map)
    color_column = resolve_optional_scatter_encoding_column(definition, 'scatter_color_column', column_id_map)
    if not isinstance(x_column, str) or x_column not in column_id_map:
        raise InvalidRequestError('Scatter plot asset definition is missing its x-axis source column.')
    if not isinstance(y_column, str) or y_column not in column_id_map:
        raise InvalidRequestError('Scatter plot asset definition is missing its y-axis source column.')
    x_dtype = schema[column_id_map[x_column]]
    y_dtype = schema[column_id_map[y_column]]
    if dtype_category(x_dtype) != 'numeric':
        raise InvalidRequestError(f'Scatter plot x column `{x_column}` must be numeric.')
    if dtype_category(y_dtype) != 'numeric':
        raise InvalidRequestError(f'Scatter plot y column `{y_column}` must be numeric.')
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selection_bounds = resolve_scatter_plot_selection_bounds(
        transient_modifiers,
        x_column=x_column,
        x_dtype=x_dtype,
        y_column=y_column,
        y_dtype=y_dtype,
    )
    selected_row_index = resolve_scatter_plot_selected_row_index(transient_modifiers)
    selected_legend = resolve_scatter_plot_selected_legend(
        transient_modifiers,
        shape_column=shape_column,
        size_column=size_column,
        color_column=color_column,
        schema=schema,
        column_id_map=column_id_map,
    )
    table_frame = filtered_frame
    if selection_bounds is not None:
        table_frame = apply_scatter_plot_selection(table_frame, x_column, y_column, selection_bounds, column_id_map)
    if selected_row_index is not None:
        table_frame = apply_scatter_plot_selected_row_index(table_frame, selected_row_index)
    if selected_legend is not None:
        table_frame = apply_scatter_plot_legend_selection(table_frame, selected_legend, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_scatter_plot_main_payload(
            filtered_frame,
            x_column=x_column,
            y_column=y_column,
            label_column=label_column,
            shape_column=shape_column,
            size_column=size_column,
            color_column=color_column,
            column_id_map=column_id_map,
            schema=schema,
        ),
        'table': prepared_table_payload(
            table_frame.select([pl.col(column_id_map[name]).alias(str(name)) for name in column_names]),
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


def prepare_scatter_plot_main_payload(
    frame: pl.LazyFrame,
    *,
    x_column: str,
    y_column: str,
    label_column: str | None,
    shape_column: str | None,
    size_column: str | None,
    color_column: str | None,
    column_id_map: dict[str, Any],
    schema: pl.Schema,
) -> dict[str, Any]:
    x_name = column_id_map[x_column]
    y_name = column_id_map[y_column]
    row_index_name = '__scatter_row_index'
    size_kind = scatter_encoding_kind(size_column, schema, column_id_map)
    rows_total_frame = frame.select(pl.len().alias('rows_total')).collect()
    rows_total = int(rows_total_frame['rows_total'][0]) if rows_total_frame.height else 0
    points_frame = frame.filter(pl.col(x_name).is_not_null() & pl.col(y_name).is_not_null())
    stats_columns: list[pl.Expr] = [
        pl.len().alias('non_null_rows'),
        pl.col(x_name).min().alias('x_min'),
        pl.col(x_name).max().alias('x_max'),
        pl.col(y_name).min().alias('y_min'),
        pl.col(y_name).max().alias('y_max'),
    ]
    if size_column is not None and size_kind == 'quantitative':
        size_name = column_id_map[size_column]
        stats_columns.extend(
            [
                pl.col(size_name).min().alias('size_min'),
                pl.col(size_name).max().alias('size_max'),
            ]
        )
    stats = points_frame.select(stats_columns).collect()
    non_null_rows = int(stats['non_null_rows'][0]) if stats.height else 0
    if non_null_rows == 0:
        return {
            'kind': 'scatter_plot',
            'x_column': x_column,
            'y_column': y_column,
            'label_column': label_column,
            'shape_column': shape_column,
            'size_column': size_column,
            'size_kind': size_kind,
            'size_domain': None,
            'color_column': color_column,
            'color_kind': scatter_encoding_kind(color_column, schema, column_id_map),
            'rows_total': rows_total,
            'non_null_rows': 0,
            'plotted_rows': 0,
            'sampled': False,
            'domain': None,
            'points': [],
        }
    x_min = float(stats['x_min'][0])
    x_max = float(stats['x_max'][0])
    y_min = float(stats['y_min'][0])
    y_max = float(stats['y_max'][0])
    sampled = non_null_rows > MAX_SCATTER_PLOT_POINTS
    point_columns: list[pl.Expr] = [
        pl.col(row_index_name).alias('row_index'),
        pl.col(x_name).alias('x'),
        pl.col(y_name).alias('y'),
    ]
    if label_column is not None:
        point_columns.append(pl.col(column_id_map[label_column]).alias('label'))
    if shape_column is not None:
        point_columns.append(pl.col(column_id_map[shape_column]).alias('shape'))
    if size_column is not None:
        point_columns.append(pl.col(column_id_map[size_column]).alias('size'))
    if color_column is not None:
        point_columns.append(pl.col(column_id_map[color_column]).alias('color'))
    points = points_frame.slice(0, MAX_SCATTER_PLOT_POINTS).select(point_columns).collect().to_dicts()
    size_domain = None
    if size_column is not None and size_kind == 'quantitative' and stats.height:
        size_min = stats['size_min'][0]
        size_max = stats['size_max'][0]
        if size_min is not None and size_max is not None:
            size_domain = {'min': float(size_min), 'max': float(size_max)}
    return {
        'kind': 'scatter_plot',
        'x_column': x_column,
        'y_column': y_column,
        'label_column': label_column,
        'shape_column': shape_column,
        'size_column': size_column,
        'size_kind': size_kind,
        'size_domain': size_domain,
        'color_column': color_column,
        'color_kind': scatter_encoding_kind(color_column, schema, column_id_map),
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'plotted_rows': len(points),
        'sampled': sampled,
        'domain': {
            'x': numeric_plot_domain(
                min_value=x_min, max_value=x_max, column=x_column, context='Scatter plot x column'
            ),
            'y': numeric_plot_domain(
                min_value=y_min, max_value=y_max, column=y_column, context='Scatter plot y column'
            ),
        },
        'points': [{str(key): json_safe_value(value) for key, value in row.items()} for row in points],
    }


def resolve_optional_scatter_encoding_column(
    definition: dict[str, Any],
    key: str,
    column_id_map: dict[str, Any],
) -> str | None:
    candidate = definition.get(key)
    if candidate is None:
        return None
    if not isinstance(candidate, str) or candidate not in column_id_map:
        raise InvalidRequestError(f'Scatter plot asset definition contains an invalid encoding column for `{key}`.')
    return candidate


def scatter_encoding_kind(
    column: str | None,
    schema: pl.Schema,
    column_id_map: dict[str, Any],
) -> str | None:
    if column is None:
        return None
    return 'quantitative' if dtype_category(schema[column_id_map[column]]) == 'numeric' else 'nominal'


def resolve_scatter_plot_selection_bounds(
    transient_modifiers: dict[str, Any],
    *,
    x_column: str,
    x_dtype: pl.DataType,
    y_column: str,
    y_dtype: pl.DataType,
) -> dict[str, dict[str, int | float]] | None:
    candidate = transient_modifiers.get('selection_bounds') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, {}):
        return None
    if not isinstance(candidate, dict):
        raise InvalidRequestError('transient_modifiers.selection_bounds must be an object.')
    x_range = candidate.get('x')
    y_range = candidate.get('y')
    if not isinstance(x_range, dict) or not isinstance(y_range, dict):
        raise InvalidRequestError('transient_modifiers.selection_bounds must define `x` and `y` objects.')
    if dtype_category(x_dtype) != 'numeric':
        raise InvalidRequestError(
            f'Scatter plot selections are only supported for numeric x columns such as `{x_column}`.'
        )
    if dtype_category(y_dtype) != 'numeric':
        raise InvalidRequestError(
            f'Scatter plot selections are only supported for numeric y columns such as `{y_column}`.'
        )
    x_lower = coerce_selection_numeric_value(x_range.get('lower'), column=x_column, kind='selection_bounds.x')
    x_upper = coerce_selection_numeric_value(x_range.get('upper'), column=x_column, kind='selection_bounds.x')
    y_lower = coerce_selection_numeric_value(y_range.get('lower'), column=y_column, kind='selection_bounds.y')
    y_upper = coerce_selection_numeric_value(y_range.get('upper'), column=y_column, kind='selection_bounds.y')
    if x_lower > x_upper:
        x_lower, x_upper = x_upper, x_lower
    if y_lower > y_upper:
        y_lower, y_upper = y_upper, y_lower
    return {
        'x': {'lower': x_lower, 'upper': x_upper},
        'y': {'lower': y_lower, 'upper': y_upper},
    }


def resolve_scatter_plot_selected_row_index(transient_modifiers: dict[str, Any]) -> int | None:
    candidate = transient_modifiers.get('selected_row_index') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, ''):
        return None
    if not isinstance(candidate, int) or candidate < 0:
        raise InvalidRequestError('transient_modifiers.selected_row_index must be a non-negative integer.')
    return candidate


def resolve_scatter_plot_selected_legend(
    transient_modifiers: dict[str, Any],
    *,
    shape_column: str | None,
    size_column: str | None,
    color_column: str | None,
    schema: pl.Schema,
    column_id_map: dict[str, Any],
) -> dict[str, Any] | None:
    candidate = transient_modifiers.get('selected_legend') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, {}):
        return None
    if not isinstance(candidate, dict):
        raise InvalidRequestError('transient_modifiers.selected_legend must be an object.')
    field = candidate.get('field')
    if field not in {'shape', 'size', 'color'}:
        raise InvalidRequestError(
            'transient_modifiers.selected_legend.field must be one of `shape`, `size`, or `color`.'
        )
    column = {
        'shape': shape_column,
        'size': size_column,
        'color': color_column,
    }[field]
    if column is None:
        raise InvalidRequestError(
            f'transient_modifiers.selected_legend.field `{field}` is not available for this scatter plot.'
        )
    value = coerce_filter_value(
        schema[column_id_map[column]],
        candidate.get('value'),
        column=column,
        kind='selected_legend',
    )
    if value is None:
        raise InvalidRequestError('transient_modifiers.selected_legend.value cannot be null.')
    return {'field': field, 'column': column, 'value': value}


def apply_scatter_plot_selection(
    frame: pl.LazyFrame,
    x_column: str,
    y_column: str,
    selection_bounds: dict[str, dict[str, int | float]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    x_name = column_id_map[x_column]
    y_name = column_id_map[y_column]
    return frame.filter(
        pl.col(x_name).is_not_null()
        & pl.col(y_name).is_not_null()
        & (pl.col(x_name) >= selection_bounds['x']['lower'])
        & (pl.col(x_name) <= selection_bounds['x']['upper'])
        & (pl.col(y_name) >= selection_bounds['y']['lower'])
        & (pl.col(y_name) <= selection_bounds['y']['upper'])
    )


def apply_scatter_plot_selected_row_index(frame: pl.LazyFrame, selected_row_index: int) -> pl.LazyFrame:
    return frame.filter(pl.col('__scatter_row_index') == selected_row_index)


def apply_scatter_plot_legend_selection(
    frame: pl.LazyFrame,
    selected_legend: dict[str, Any],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[selected_legend['column']]
    return frame.filter(
        pl.col(column_name).is_not_null() & (pl.col(column_name) == selected_legend['value']).fill_null(False)
    )


def coerce_selection_numeric_value(value: object, *, column: str, kind: str) -> int | float:
    if isinstance(value, bool):
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects numeric values.')
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects numeric values.')
