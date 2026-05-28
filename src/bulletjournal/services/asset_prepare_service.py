from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from bulletjournal.domain.errors import InvalidRequestError, NotFoundError

_ALLOWED_PAGE_SIZES = {10, 25, 50, 100}
_DEFAULT_PAGE_SIZE = 25
_DEFAULT_HISTOGRAM_BIN_COUNT = 20
_MAX_HISTOGRAM_BIN_COUNT = 100
_MAX_SCATTER_PLOT_POINTS = 2_000
_MAX_PREPARED_RESPONSE_BYTES = 1_000_000
_DEFAULT_PIE_CHART_COLOR = '#94a3b8'
_DEFAULT_PIE_CHART_PALETTE = [
    '#2563eb',
    '#14b8a6',
    '#f59e0b',
    '#ef4444',
    '#8b5cf6',
    '#06b6d4',
    '#84cc16',
    '#f97316',
]


class AssetPrepareService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def prepare_asset(
        self,
        node_id: str,
        asset_name: str,
        *,
        asset_version_id: int | None,
        modifier_overrides: dict[str, Any],
        transient_modifiers: dict[str, Any],
        panel_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del panel_context
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        current_asset_version_id = head.get('current_asset_version_id')
        if current_asset_version_id is None or head.get('definition') is None:
            raise InvalidRequestError(f'Asset `{node_id}/{asset_name}` has not been produced yet.')
        asset_type = head.get('asset_type')
        if asset_type not in {'dataframe', 'histogram', 'pie_chart', 'scatter_plot'}:
            raise InvalidRequestError(
                'Asset prepare is only supported for interactive dataframe, '
                'histogram, pie chart, and scatter plot assets in this release.'
            )
        if not isinstance(modifier_overrides, dict):
            raise InvalidRequestError('modifier_overrides must be an object.')
        if not isinstance(transient_modifiers, dict):
            raise InvalidRequestError('transient_modifiers must be an object.')
        errors: list[dict[str, str]] = []
        if asset_version_id is not None and asset_version_id != current_asset_version_id:
            errors.append(
                {
                    'code': 'asset_version_mismatch',
                    'message': (
                        f'Asset `{node_id}/{asset_name}` moved from version {asset_version_id} '
                        f'to version {current_asset_version_id}.'
                    ),
                }
            )
        project = self.project_service.require_project()
        dataset_object = _backing_dataset_object(head.get('objects'))
        project.state_db.touch_artifact_object(dataset_object['artifact_hash'])
        dataset_path = project.object_store.load_file_path(str(dataset_object['artifact_hash']))
        if asset_type == 'dataframe':
            table_payload, resolved_modifiers = _prepare_dataframe_payload(
                dataset_path=dataset_path,
                default_modifiers=head.get('default_modifiers') or {},
                modifier_overrides=modifier_overrides,
            )
            payloads: dict[str, Any] = {'table': table_payload}
        elif asset_type == 'histogram':
            payloads, resolved_modifiers = _prepare_histogram_payload(
                dataset_path=dataset_path,
                definition=head.get('definition') or {},
                default_modifiers=head.get('default_modifiers') or {},
                modifier_overrides=modifier_overrides,
                transient_modifiers=transient_modifiers,
            )
        elif asset_type == 'pie_chart':
            payloads, resolved_modifiers = _prepare_pie_chart_payload(
                dataset_path=dataset_path,
                definition=head.get('definition') or {},
                default_modifiers=head.get('default_modifiers') or {},
                modifier_overrides=modifier_overrides,
                transient_modifiers=transient_modifiers,
            )
        else:
            payloads, resolved_modifiers = _prepare_scatter_plot_payload(
                dataset_path=dataset_path,
                definition=head.get('definition') or {},
                default_modifiers=head.get('default_modifiers') or {},
                modifier_overrides=modifier_overrides,
                transient_modifiers=transient_modifiers,
            )
        response = {
            'asset_version_id': int(current_asset_version_id),
            'state': head['state'],
            'resolved_modifiers': resolved_modifiers,
            'override_schema_hash': head.get('override_schema_hash'),
            'payloads': payloads,
            'errors': errors,
        }
        if len(json.dumps(response, ensure_ascii=True).encode('utf-8')) > _MAX_PREPARED_RESPONSE_BYTES:
            raise InvalidRequestError('Prepared asset response exceeds the 1 MB cap.')
        return response


def _backing_dataset_object(objects: object) -> dict[str, Any]:
    if not isinstance(objects, list):
        raise InvalidRequestError('Asset backing dataset metadata is missing.')
    for item in objects:
        if isinstance(item, dict) and item.get('object_role') == 'backing_dataset' and item.get('artifact_hash'):
            return item
    raise InvalidRequestError('Asset backing dataset is missing.')


def _prepare_dataframe_payload(
    *,
    dataset_path: Path,
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    frame = pl.scan_parquet(dataset_path)
    schema = frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    resolved_page = _resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = _resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = _resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    table_frame = _frame_with_filters(frame, resolved_filters, column_id_map)
    table_frame = _frame_with_sort(table_frame, resolved_sort, column_id_map)
    return _prepared_table_payload(
        table_frame,
        schema=schema,
        column_names=column_names,
        resolved_page=resolved_page,
        resolved_sort=resolved_sort,
    ), {
        'page': resolved_page,
        'sort': resolved_sort,
        'filters': resolved_filters,
    }


def _prepare_histogram_payload(
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
    if _dtype_category(histogram_dtype) != 'numeric':
        raise InvalidRequestError(f'Histogram column `{histogram_column}` must be numeric.')
    resolved_page = _resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = _resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = _resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_bin_count = _resolve_bin_count(default_modifiers, modifier_overrides)
    filtered_frame = _frame_with_filters(frame, resolved_filters, column_id_map)
    selection_ranges = _resolve_histogram_selection_ranges(
        transient_modifiers,
        column=histogram_column,
        dtype=histogram_dtype,
    )
    table_frame = filtered_frame
    if selection_ranges:
        table_frame = _apply_histogram_selections(table_frame, histogram_column, selection_ranges, column_id_map)
    table_frame = _frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': _prepare_histogram_main_payload(
            filtered_frame,
            column=histogram_column,
            column_id_map=column_id_map,
            bin_count=resolved_bin_count,
        ),
        'table': _prepared_table_payload(
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


def _prepare_scatter_plot_payload(
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
    shape_column = _resolve_optional_scatter_encoding_column(definition, 'scatter_shape_column', column_id_map)
    size_column = _resolve_optional_scatter_encoding_column(definition, 'scatter_size_column', column_id_map)
    color_column = _resolve_optional_scatter_encoding_column(definition, 'scatter_color_column', column_id_map)
    if not isinstance(x_column, str) or x_column not in column_id_map:
        raise InvalidRequestError('Scatter plot asset definition is missing its x-axis source column.')
    if not isinstance(y_column, str) or y_column not in column_id_map:
        raise InvalidRequestError('Scatter plot asset definition is missing its y-axis source column.')
    x_dtype = schema[column_id_map[x_column]]
    y_dtype = schema[column_id_map[y_column]]
    if _dtype_category(x_dtype) != 'numeric':
        raise InvalidRequestError(f'Scatter plot x column `{x_column}` must be numeric.')
    if _dtype_category(y_dtype) != 'numeric':
        raise InvalidRequestError(f'Scatter plot y column `{y_column}` must be numeric.')
    resolved_page = _resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = _resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = _resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    filtered_frame = _frame_with_filters(frame, resolved_filters, column_id_map)
    selection_bounds = _resolve_scatter_plot_selection_bounds(
        transient_modifiers,
        x_column=x_column,
        x_dtype=x_dtype,
        y_column=y_column,
        y_dtype=y_dtype,
    )
    selected_row_index = _resolve_scatter_plot_selected_row_index(transient_modifiers)
    table_frame = filtered_frame
    if selection_bounds is not None:
        table_frame = _apply_scatter_plot_selection(table_frame, x_column, y_column, selection_bounds, column_id_map)
    if selected_row_index is not None:
        table_frame = _apply_scatter_plot_selected_row_index(table_frame, selected_row_index)
    table_frame = _frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': _prepare_scatter_plot_main_payload(
            filtered_frame,
            x_column=x_column,
            y_column=y_column,
            shape_column=shape_column,
            size_column=size_column,
            color_column=color_column,
            column_id_map=column_id_map,
            schema=schema,
        ),
        'table': _prepared_table_payload(
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


def _prepare_pie_chart_payload(
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
    resolved_page = _resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = _resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = _resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    filtered_frame = _frame_with_filters(frame, resolved_filters, column_id_map)
    selected_categories = _resolve_pie_chart_selected_categories(
        transient_modifiers,
        column=category_column,
        dtype=category_dtype,
    )
    table_frame = filtered_frame
    if selected_categories:
        table_frame = _apply_pie_chart_selection(table_frame, category_column, selected_categories, column_id_map)
    table_frame = _frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': _prepare_pie_chart_main_payload(
            filtered_frame,
            column=category_column,
            column_id_map=column_id_map,
            color_mapping_entries=definition.get('pie_color_mapping'),
            default_color=definition.get('pie_default_color'),
        ),
        'table': _prepared_table_payload(
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


def _prepared_table_payload(
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
                'filter_kinds': _column_filter_kinds(schema[name]),
            }
            for name in column_names
        ],
        'page': resolved_page,
        'sort': resolved_sort,
        'rows': [{str(key): _json_safe_value(value) for key, value in row.items()} for row in rows],
    }


def _frame_with_filters(
    frame: pl.LazyFrame,
    resolved_filters: list[dict[str, Any]],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    for filter_entry in resolved_filters:
        frame = _apply_filter(frame, filter_entry, column_id_map)
    return frame


def _frame_with_sort(
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


def _prepare_histogram_main_payload(
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
    if not stats.height:
        return {
            'kind': 'histogram',
            'x_column': column,
            'rows_total': 0,
            'non_null_rows': 0,
            'bin_count': bin_count,
            'domain': None,
            'bins': [],
        }
    rows_total = int(stats['rows_total'][0])
    non_null_rows = int(stats['non_null_rows'][0])
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
        bins = [
            {
                'index': 0,
                'start': _json_safe_value(min_value - 0.5),
                'end': _json_safe_value(max_value + 0.5),
                'count': non_null_rows,
            }
        ]
        return {
            'kind': 'histogram',
            'x_column': column,
            'rows_total': rows_total,
            'non_null_rows': non_null_rows,
            'bin_count': 1,
            'domain': {'min': _json_safe_value(min_value - 0.5), 'max': _json_safe_value(max_value + 0.5)},
            'bins': bins,
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
                'start': _json_safe_value(start),
                'end': _json_safe_value(end),
                'count': counts_by_index.get(index, 0),
            }
        )
    return {
        'kind': 'histogram',
        'x_column': column,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'bin_count': bin_count,
        'domain': {'min': _json_safe_value(min_value), 'max': _json_safe_value(max_value)},
        'bins': bins,
    }


def _prepare_scatter_plot_main_payload(
    frame: pl.LazyFrame,
    *,
    x_column: str,
    y_column: str,
    shape_column: str | None,
    size_column: str | None,
    color_column: str | None,
    column_id_map: dict[str, Any],
    schema: pl.Schema,
) -> dict[str, Any]:
    x_name = column_id_map[x_column]
    y_name = column_id_map[y_column]
    row_index_name = '__scatter_row_index'
    rows_total_frame = frame.select(pl.len().alias('rows_total')).collect()
    rows_total = int(rows_total_frame['rows_total'][0]) if rows_total_frame.height else 0
    points_frame = frame.filter(pl.col(x_name).is_not_null() & pl.col(y_name).is_not_null())
    stats = points_frame.select(
        [
            pl.len().alias('non_null_rows'),
            pl.col(x_name).min().alias('x_min'),
            pl.col(x_name).max().alias('x_max'),
            pl.col(y_name).min().alias('y_min'),
            pl.col(y_name).max().alias('y_max'),
        ]
    ).collect()
    if not stats.height:
        return {
            'kind': 'scatter_plot',
            'x_column': x_column,
            'y_column': y_column,
            'shape_column': shape_column,
            'size_column': size_column,
            'size_kind': _scatter_encoding_kind(size_column, schema, column_id_map),
            'color_column': color_column,
            'color_kind': _scatter_encoding_kind(color_column, schema, column_id_map),
            'rows_total': rows_total,
            'non_null_rows': 0,
            'plotted_rows': 0,
            'sampled': False,
            'domain': None,
            'points': [],
        }
    non_null_rows = int(stats['non_null_rows'][0])
    if non_null_rows == 0:
        return {
            'kind': 'scatter_plot',
            'x_column': x_column,
            'y_column': y_column,
            'shape_column': shape_column,
            'size_column': size_column,
            'size_kind': _scatter_encoding_kind(size_column, schema, column_id_map),
            'color_column': color_column,
            'color_kind': _scatter_encoding_kind(color_column, schema, column_id_map),
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
    x_domain = _numeric_plot_domain(min_value=x_min, max_value=x_max, column=x_column, context='Scatter plot x column')
    y_domain = _numeric_plot_domain(min_value=y_min, max_value=y_max, column=y_column, context='Scatter plot y column')
    sampled = non_null_rows > _MAX_SCATTER_PLOT_POINTS
    point_columns: list[pl.Expr] = [
        pl.col(row_index_name).alias('row_index'),
        pl.col(x_name).alias('x'),
        pl.col(y_name).alias('y'),
    ]
    if shape_column is not None:
        point_columns.append(pl.col(column_id_map[shape_column]).alias('shape'))
    if size_column is not None:
        point_columns.append(pl.col(column_id_map[size_column]).alias('size'))
    if color_column is not None:
        point_columns.append(pl.col(column_id_map[color_column]).alias('color'))
    points = points_frame.slice(0, _MAX_SCATTER_PLOT_POINTS).select(point_columns).collect().to_dicts()
    return {
        'kind': 'scatter_plot',
        'x_column': x_column,
        'y_column': y_column,
        'shape_column': shape_column,
        'size_column': size_column,
        'size_kind': _scatter_encoding_kind(size_column, schema, column_id_map),
        'color_column': color_column,
        'color_kind': _scatter_encoding_kind(color_column, schema, column_id_map),
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'plotted_rows': len(points),
        'sampled': sampled,
        'domain': {'x': x_domain, 'y': y_domain},
        'points': [{str(key): _json_safe_value(value) for key, value in row.items()} for row in points],
    }


def _prepare_pie_chart_main_payload(
    frame: pl.LazyFrame,
    *,
    column: str,
    column_id_map: dict[str, Any],
    color_mapping_entries: object,
    default_color: object,
) -> dict[str, Any]:
    column_name = column_id_map[column]
    explicit_color_mapping = _pie_chart_color_mapping_from_definition(color_mapping_entries)
    resolved_default_color = (
        default_color if isinstance(default_color, str) and default_color else _DEFAULT_PIE_CHART_COLOR
    )
    stats = frame.select(
        [
            pl.len().alias('rows_total'),
            pl.col(column_name).count().alias('non_null_rows'),
        ]
    ).collect()
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
        frame.filter(pl.col(column_name).is_not_null())
        .group_by(column_name)
        .agg(pl.len().alias('count'))
        .sort(['count', column_name], descending=[True, False])
        .collect()
    )
    slices: list[dict[str, Any]] = []
    for row in grouped.to_dicts():
        value = _json_safe_value(row.get(column_name))
        if value is None:
            continue
        count = int(row['count'])
        slices.append(
            {
                'value': value,
                'label': value if isinstance(value, str) else str(value),
                'count': count,
                'share': count / non_null_rows,
                'color': _pie_chart_color_for_value(
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


def _pie_chart_color_mapping_from_definition(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    mapping: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        color = entry.get('color')
        if not isinstance(color, str) or not color:
            continue
        key = _pie_chart_color_mapping_key(_json_safe_value(entry.get('value')))
        mapping[key] = color
    return mapping


def _pie_chart_color_for_value(
    value: object,
    *,
    index: int,
    explicit_color_mapping: dict[str, str],
    default_color: str,
) -> str:
    key = _pie_chart_color_mapping_key(value)
    if explicit_color_mapping:
        return explicit_color_mapping.get(key, default_color)
    return _DEFAULT_PIE_CHART_PALETTE[index % len(_DEFAULT_PIE_CHART_PALETTE)]


def _pie_chart_color_mapping_key(value: object) -> str:
    return json.dumps(_json_safe_value(value), ensure_ascii=True, sort_keys=True)


def _numeric_plot_domain(
    *, min_value: float, max_value: float, column: str, context: str
) -> dict[str, int | float | None]:
    if not math.isfinite(min_value) or not math.isfinite(max_value):
        raise InvalidRequestError(f'{context} `{column}` contains non-finite numeric values.')
    if min_value == max_value:
        return {'min': _json_safe_value(min_value - 0.5), 'max': _json_safe_value(max_value + 0.5)}
    return {'min': _json_safe_value(min_value), 'max': _json_safe_value(max_value)}


def _resolve_optional_scatter_encoding_column(
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


def _scatter_encoding_kind(
    column: str | None,
    schema: pl.Schema,
    column_id_map: dict[str, Any],
) -> str | None:
    if column is None:
        return None
    return 'quantitative' if _dtype_category(schema[column_id_map[column]]) == 'numeric' else 'nominal'


def _resolve_page(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> dict[str, int]:
    default_page = default_modifiers.get('page') if isinstance(default_modifiers, dict) else None
    page_index = _coerce_page_index(default_page.get('index') if isinstance(default_page, dict) else 0)
    page_size = _coerce_page_size(default_page.get('size') if isinstance(default_page, dict) else _DEFAULT_PAGE_SIZE)
    if 'page' in modifier_overrides:
        page_override = modifier_overrides['page']
        if not isinstance(page_override, dict):
            raise InvalidRequestError('modifier_overrides.page must be an object.')
        page_index = _coerce_page_index(page_override.get('index', page_index))
        page_size = _coerce_page_size(page_override.get('size', page_size))
    return {'index': page_index, 'size': page_size}


def _resolve_sort(
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


def _resolve_bin_count(default_modifiers: dict[str, Any], modifier_overrides: dict[str, Any]) -> int:
    candidate = (
        default_modifiers.get('bin_count', _DEFAULT_HISTOGRAM_BIN_COUNT)
        if isinstance(default_modifiers, dict)
        else _DEFAULT_HISTOGRAM_BIN_COUNT
    )
    if 'bin_count' in modifier_overrides:
        candidate = modifier_overrides['bin_count']
    return _coerce_bin_count(candidate)


def _resolve_filters(
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
        resolved.append(_resolve_filter_entry(column=column, kind=str(kind), dtype=dtype, entry=entry))
        seen_columns.add(column)
    return resolved


def _resolve_histogram_selection_ranges(
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
    if _dtype_category(dtype) != 'numeric':
        raise InvalidRequestError(f'Histogram selections are only supported for numeric columns such as `{column}`.')
    resolved_ranges: list[dict[str, int | float]] = []
    for entry in raw_ranges:
        if not isinstance(entry, dict):
            raise InvalidRequestError('transient_modifiers.selection_ranges entries must be objects.')
        lower = _coerce_numeric_filter_value(entry.get('lower'), column=column, kind='selection_range')
        upper = _coerce_numeric_filter_value(entry.get('upper'), column=column, kind='selection_range')
        if lower > upper:
            lower, upper = upper, lower
        resolved_ranges.append({'lower': lower, 'upper': upper})
    return resolved_ranges


def _resolve_scatter_plot_selection_bounds(
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
    if _dtype_category(x_dtype) != 'numeric':
        raise InvalidRequestError(
            f'Scatter plot selections are only supported for numeric x columns such as `{x_column}`.'
        )
    if _dtype_category(y_dtype) != 'numeric':
        raise InvalidRequestError(
            f'Scatter plot selections are only supported for numeric y columns such as `{y_column}`.'
        )
    x_lower = _coerce_numeric_filter_value(x_range.get('lower'), column=x_column, kind='selection_bounds.x')
    x_upper = _coerce_numeric_filter_value(x_range.get('upper'), column=x_column, kind='selection_bounds.x')
    y_lower = _coerce_numeric_filter_value(y_range.get('lower'), column=y_column, kind='selection_bounds.y')
    y_upper = _coerce_numeric_filter_value(y_range.get('upper'), column=y_column, kind='selection_bounds.y')
    if x_lower > x_upper:
        x_lower, x_upper = x_upper, x_lower
    if y_lower > y_upper:
        y_lower, y_upper = y_upper, y_lower
    return {
        'x': {'lower': x_lower, 'upper': x_upper},
        'y': {'lower': y_lower, 'upper': y_upper},
    }


def _resolve_scatter_plot_selected_row_index(transient_modifiers: dict[str, Any]) -> int | None:
    candidate = transient_modifiers.get('selected_row_index') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, ''):
        return None
    if not isinstance(candidate, int) or candidate < 0:
        raise InvalidRequestError('transient_modifiers.selected_row_index must be a non-negative integer.')
    return candidate


def _resolve_pie_chart_selected_categories(
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
        coerced = _coerce_filter_value(dtype, value, column=column, kind='selected_categories')
        if coerced is None:
            raise InvalidRequestError('transient_modifiers.selected_categories cannot contain null values.')
        value_key = json.dumps(_json_safe_value(coerced), ensure_ascii=True, sort_keys=True)
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        resolved.append(coerced)
    return resolved


def _resolve_filter_entry(
    *,
    column: str,
    kind: str,
    dtype: pl.DataType,
    entry: dict[str, Any],
) -> dict[str, Any]:
    category = _dtype_category(dtype)
    if kind == 'range':
        if category not in {'numeric', 'date', 'datetime', 'time'}:
            raise InvalidRequestError(f'Range filters are not supported for column `{column}`.')
        lower = _coerce_filter_value(dtype, entry.get('lower'), column=column, kind='range')
        upper = _coerce_filter_value(dtype, entry.get('upper'), column=column, kind='range')
        if lower is None and upper is None:
            raise InvalidRequestError(f'Range filter `{column}` must define `lower`, `upper`, or both.')
        return {
            'kind': 'range',
            'column': column,
            'value_type': category,
            'lower': _json_safe_value(lower),
            'upper': _json_safe_value(upper),
        }
    if kind == 'value':
        raw_values = entry.get('values')
        if raw_values is None:
            raw_values = []
        if not isinstance(raw_values, list):
            raise InvalidRequestError(f'Value filter `{column}` must define `values` as an array.')
        include_null = bool(entry.get('include_null', False))
        resolved_values = [
            _coerce_filter_value(dtype, item, column=column, kind='value') for item in raw_values if item is not None
        ]
        if not resolved_values and not include_null:
            raise InvalidRequestError(f'Value filter `{column}` must define at least one value or include nulls.')
        return {
            'kind': 'value',
            'column': column,
            'value_type': category,
            'values': [_json_safe_value(item) for item in resolved_values],
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


def _apply_filter(frame: pl.LazyFrame, filter_entry: dict[str, Any], column_id_map: dict[str, Any]) -> pl.LazyFrame:
    column_name = column_id_map[filter_entry['column']]
    expression = pl.col(column_name)
    kind = filter_entry['kind']
    if kind == 'range':
        value_type = str(filter_entry.get('value_type') or '')
        lower = _restore_filter_value(filter_entry.get('lower'), value_type=value_type)
        upper = _restore_filter_value(filter_entry.get('upper'), value_type=value_type)
        if lower is not None:
            frame = frame.filter(expression >= lower)
        if upper is not None:
            frame = frame.filter(expression <= upper)
        return frame
    if kind == 'value':
        value_type = str(filter_entry.get('value_type') or '')
        values = [_restore_filter_value(item, value_type=value_type) for item in filter_entry.get('values', [])]
        predicate = expression.is_in(values).fill_null(False) if values else pl.lit(False)
        if bool(filter_entry.get('include_null', False)):
            predicate = predicate | expression.is_null()
        return frame.filter(predicate)
    pattern = str(filter_entry['pattern'])
    if not bool(filter_entry.get('case_sensitive', False)):
        pattern = f'(?i){pattern}'
    return frame.filter(expression.cast(pl.Utf8).str.contains(pattern).fill_null(False))


def _apply_histogram_selections(
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


def _apply_scatter_plot_selection(
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


def _apply_scatter_plot_selected_row_index(frame: pl.LazyFrame, selected_row_index: int) -> pl.LazyFrame:
    return frame.filter(pl.col('__scatter_row_index') == selected_row_index)


def _apply_pie_chart_selection(
    frame: pl.LazyFrame,
    column: str,
    selected_categories: list[Any],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    return frame.filter(
        pl.col(column_name).is_not_null() & pl.col(column_name).is_in(selected_categories).fill_null(False)
    )


def _coerce_page_index(value: object) -> int:
    if not isinstance(value, int) or value < 0:
        raise InvalidRequestError('Page index must be a zero-based integer.')
    return value


def _coerce_bin_count(value: object) -> int:
    if not isinstance(value, int) or value < 1 or value > _MAX_HISTOGRAM_BIN_COUNT:
        raise InvalidRequestError(f'Histogram bin count must be an integer between 1 and {_MAX_HISTOGRAM_BIN_COUNT}.')
    return value


def _coerce_page_size(value: object) -> int:
    if not isinstance(value, int) or value not in _ALLOWED_PAGE_SIZES:
        allowed = ', '.join(str(size) for size in sorted(_ALLOWED_PAGE_SIZES))
        raise InvalidRequestError(f'Page size must be one of: {allowed}.')
    return value


def _column_filter_kinds(dtype: pl.DataType) -> list[str]:
    category = _dtype_category(dtype)
    if category in {'numeric', 'date', 'datetime', 'time'}:
        return ['range', 'value']
    if category == 'bool':
        return ['value']
    return ['value', 'regex']


def _dtype_category(dtype: pl.DataType) -> str:
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


def _coerce_filter_value(dtype: pl.DataType, value: object, *, column: str, kind: str) -> Any:
    if value is None:
        return None
    category = _dtype_category(dtype)
    if category == 'numeric':
        return _coerce_numeric_filter_value(value, column=column, kind=kind)
    if category == 'date':
        return _coerce_date_filter_value(value, column=column, kind=kind)
    if category == 'datetime':
        return _coerce_datetime_filter_value(value, column=column, kind=kind)
    if category == 'time':
        return _coerce_time_filter_value(value, column=column, kind=kind)
    if category == 'bool':
        return _coerce_bool_filter_value(value, column=column, kind=kind)
    if isinstance(value, str):
        return value
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects string values.')


def _coerce_numeric_filter_value(value: object, *, column: str, kind: str) -> int | float:
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


def _coerce_date_filter_value(value: object, *, column: str, kind: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO date strings.')
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO date strings.') from exc


def _coerce_datetime_filter_value(value: object, *, column: str, kind: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO datetime strings.')
    normalized = value.strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO datetime strings.') from exc


def _coerce_time_filter_value(value: object, *, column: str, kind: str) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO time strings.')
    try:
        return time.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects ISO time strings.') from exc


def _coerce_bool_filter_value(value: object, *, column: str, kind: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == 'true':
            return True
        if normalized == 'false':
            return False
    raise InvalidRequestError(f'Filter `{kind}` for column `{column}` expects boolean values.')


def _restore_filter_value(value: object, *, value_type: str) -> Any:
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


def _json_safe_value(value: Any) -> Any:
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
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return _json_safe_value(normalized)
    isoformat = getattr(value, 'isoformat', None)
    if callable(isoformat):
        try:
            return isoformat()
        except TypeError:
            pass
    return str(value)
