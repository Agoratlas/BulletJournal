from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from bulletjournal.assets.runtime_types import (
    BaseAsset,
    DataFrameAsset,
    HistogramAsset,
    MarkdownAsset,
    PieChartAsset,
    ScatterPlotAsset,
    asset_type_id_for_instance,
    normalize_pie_chart_color_mapping,
)
from bulletjournal.storage.object_store import ObjectStore


@dataclass(slots=True)
class SerializedAssetObject:
    object_role: str
    persisted: dict[str, Any]
    object_index: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class SerializedAssetVersion:
    asset_type: str
    interactive: bool
    definition: dict[str, Any]
    modifier_schema: list[dict[str, Any]]
    default_modifiers: dict[str, Any]
    objects: list[SerializedAssetObject]


def serialize_asset(
    asset: BaseAsset,
    *,
    object_store: ObjectStore,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    asset_type = asset_type_id_for_instance(asset)
    if asset_type is None:
        raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')
    modifier_schema: list[dict[str, Any]] = []
    default_modifiers: dict[str, Any] = {}
    base_definition = {
        'asset_type': asset_type,
        'interactive': bool(getattr(asset.__class__, 'interactive', False)),
        'display_title': True,
        'description': description,
        'supports_table_view': False,
        'modifier_defaults': default_modifiers,
        'modifier_schema': modifier_schema,
        'interaction_bindings': [],
        'data_dependencies': [],
    }
    if isinstance(asset, MarkdownAsset):
        return SerializedAssetVersion(
            asset_type=asset_type,
            interactive=False,
            definition={
                **base_definition,
                'markdown_text': asset.text,
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[],
        )
    if isinstance(asset, DataFrameAsset):
        persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
        column_definitions = _dataframe_column_definitions(asset.dataframe)
        default_modifiers = {
            'page': {'index': 0, 'size': 25},
            'sort': [],
            'filters': [],
        }
        modifier_schema = _dataset_modifier_schema(
            column_definitions,
            default_modifiers,
            filters_targets=['table'],
        )
        return SerializedAssetVersion(
            asset_type=asset_type,
            interactive=True,
            definition={
                **base_definition,
                'interactive': True,
                'supports_table_view': True,
                'modifier_defaults': default_modifiers,
                'modifier_schema': modifier_schema,
                'data_dependencies': ['backing_dataset'],
                'table_columns': [str(column) for column in asset.dataframe.columns],
                'table_column_types': {column['id']: column['data_type'] for column in column_definitions},
                'row_count': int(asset.dataframe.shape[0]),
                'object_role': 'backing_dataset',
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
        )
    if isinstance(asset, HistogramAsset):
        persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
        column_definitions = _dataframe_column_definitions(asset.dataframe)
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
            **_merge_modifier_defaults(
                _histogram_chart_modifier_defaults(
                    title=title,
                    x_column=str(asset.x),
                    y_axis_label='Rows',
                    bin_count=int(asset.bins),
                ),
                asset.modifier_defaults,
            ),
        }
        modifier_schema = [
            *_dataset_modifier_schema(
                column_definitions,
                default_modifiers,
                filters_targets=['main', 'table'],
            ),
            *_histogram_chart_modifier_schema(default_modifiers),
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
            asset_type=asset_type,
            interactive=True,
            definition={
                **base_definition,
                'interactive': True,
                'supports_table_view': True,
                'modifier_defaults': default_modifiers,
                'modifier_schema': modifier_schema,
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
                'histogram_size_column_type': str(asset.dataframe.dtypes[asset.size])
                if asset.size is not None
                else None,
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
    if isinstance(asset, PieChartAsset):
        persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
        column_definitions = _dataframe_column_definitions(asset.dataframe)
        color_mapping = [
            {'value': _json_safe_modifier_value(category_value), 'color': color_value}
            for category_value, color_value in normalize_pie_chart_color_mapping(
                asset.dataframe,
                category_column=str(asset.category),
                color=asset.color,
            )
        ]
        encodings = {
            'color': {
                'column': str(asset.category),
                'data_type': str(asset.dataframe.dtypes[asset.category]),
                'kind': 'nominal',
            },
            'theta': {
                'aggregate': 'count',
                'kind': 'quantitative',
            },
        }
        default_modifiers = {
            'page': {'index': 0, 'size': 10},
            'sort': [],
            'filters': [],
            **_merge_modifier_defaults(
                _pie_chart_modifier_defaults(
                    title=title,
                    category_column=str(asset.category),
                ),
                asset.modifier_defaults,
            ),
        }
        modifier_schema = [
            *_dataset_modifier_schema(
                column_definitions,
                default_modifiers,
                filters_targets=['main', 'table'],
            ),
            *_pie_chart_modifier_schema(default_modifiers),
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
            asset_type=asset_type,
            interactive=True,
            definition={
                **base_definition,
                'interactive': True,
                'supports_table_view': True,
                'modifier_defaults': default_modifiers,
                'modifier_schema': modifier_schema,
                'interaction_bindings': [
                    {
                        'modifier_id': 'selected_categories',
                        'source': 'vega_event',
                        'event_name': 'slice_click',
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
                    'inner_radius': default_modifiers['inner_radius'],
                    'label_threshold': default_modifiers['label_threshold'],
                    'label_position': default_modifiers['label_position'],
                    'merge_threshold': default_modifiers['merge_threshold'],
                    'border_thickness': default_modifiers['border_thickness'],
                },
                'vega_template_kind': 'pie_chart',
                'pie_category_column': str(asset.category),
                'pie_category_column_type': str(asset.dataframe.dtypes[asset.category]),
                'pie_color_column': asset.color if isinstance(asset.color, str) else None,
                'pie_color_mapping': color_mapping,
                'pie_default_color': '#94a3b8',
                'object_role': 'backing_dataset',
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
        )
    if isinstance(asset, ScatterPlotAsset):
        persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
        column_definitions = _dataframe_column_definitions(asset.dataframe)
        encodings = {
            'x': {
                'column': str(asset.x),
                'data_type': str(asset.dataframe.dtypes[asset.x]),
                'kind': 'quantitative',
            },
            'y': {
                'column': str(asset.y),
                'data_type': str(asset.dataframe.dtypes[asset.y]),
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
            **_merge_modifier_defaults(
                _scatter_plot_chart_modifier_defaults(
                    title=title,
                    x_column=str(asset.x),
                    y_column=str(asset.y),
                ),
                asset.modifier_defaults,
            ),
        }
        modifier_schema = [
            *_dataset_modifier_schema(
                column_definitions,
                default_modifiers,
                filters_targets=['main', 'table'],
            ),
            *_scatter_plot_chart_modifier_schema(default_modifiers),
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
            asset_type=asset_type,
            interactive=True,
            definition={
                **base_definition,
                'interactive': True,
                'supports_table_view': True,
                'modifier_defaults': default_modifiers,
                'modifier_schema': modifier_schema,
                'interaction_bindings': [
                    {
                        'modifier_id': 'selection_bounds',
                        'source': 'vega_signal',
                        'signal_name': 'selection_bounds',
                        'category': 'transient_view',
                        'server_targets': ['table'],
                    },
                    {
                        'modifier_id': 'selected_row_index',
                        'source': 'vega_event',
                        'event_name': 'point_click',
                        'category': 'transient_view',
                        'server_targets': ['table'],
                    },
                ],
                'data_dependencies': ['backing_dataset'],
                'table_columns': [str(column) for column in asset.dataframe.columns],
                'table_column_types': {column['id']: column['data_type'] for column in column_definitions},
                'row_count': int(asset.dataframe.shape[0]),
                'dataset_binding': {'object_role': 'backing_dataset'},
                'encodings': encodings,
                'visual_defaults': {
                    'point_size': 60,
                    'point_opacity': 0.85,
                },
                'vega_template_kind': 'scatter_plot',
                'scatter_x_column': str(asset.x),
                'scatter_x_column_type': str(asset.dataframe.dtypes[asset.x]),
                'scatter_y_column': str(asset.y),
                'scatter_y_column_type': str(asset.dataframe.dtypes[asset.y]),
                'scatter_shape_column': str(asset.shape) if asset.shape is not None else None,
                'scatter_shape_column_type': str(asset.dataframe.dtypes[asset.shape])
                if asset.shape is not None
                else None,
                'scatter_size_column': str(asset.size) if asset.size is not None else None,
                'scatter_size_column_type': str(asset.dataframe.dtypes[asset.size]) if asset.size is not None else None,
                'scatter_color_column': str(asset.color) if asset.color is not None else None,
                'scatter_color_column_type': str(asset.dataframe.dtypes[asset.color])
                if asset.color is not None
                else None,
                'object_role': 'backing_dataset',
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
        )
    raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')


def _dataset_modifier_schema(
    column_definitions: list[dict[str, Any]],
    default_modifiers: dict[str, Any],
    *,
    filters_targets: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            'id': 'page',
            'title': 'Page',
            'kind': 'page',
            'category': 'saved_query',
            'server_targets': ['table'],
            'default_value': default_modifiers['page'],
        },
        {
            'id': 'sort',
            'title': 'Sort',
            'kind': 'sort',
            'category': 'saved_query',
            'server_targets': ['table'],
            'default_value': default_modifiers['sort'],
            'columns': [
                {
                    'id': column['id'],
                    'title': column['title'],
                }
                for column in column_definitions
            ],
        },
        {
            'id': 'filters',
            'title': 'Filters',
            'kind': 'filters',
            'category': 'saved_query',
            'server_targets': filters_targets,
            'default_value': default_modifiers['filters'],
            'columns': column_definitions,
        },
    ]


def _histogram_chart_modifier_defaults(
    *, title: str, x_column: str, y_axis_label: str, bin_count: int
) -> dict[str, Any]:
    return {
        'bin_count': bin_count,
        'bar_width': 90,
        'border_thickness': 0,
        'x_axis': _axis_modifier_defaults(x_column),
        'y_axis': _axis_modifier_defaults(y_axis_label),
        'title': _title_modifier_defaults(title),
    }


def _scatter_plot_chart_modifier_defaults(*, title: str, x_column: str, y_column: str) -> dict[str, Any]:
    return {
        'min_point_size': 60,
        'max_point_size': 400,
        'show_legend': True,
        'shape_style': 'outline',
        'x_axis': _axis_modifier_defaults(x_column),
        'y_axis': _axis_modifier_defaults(y_column),
        'title': _title_modifier_defaults(title),
    }


def _pie_chart_modifier_defaults(*, title: str, category_column: str) -> dict[str, Any]:
    del category_column
    return {
        'inner_radius': 0.5,
        'label_size': 20,
        'label_threshold': 5,
        'label_position': 102,
        'merge_threshold': 0,
        'border_thickness': 3,
        'merged_category_label': 'Others',
        'show_merged_category': True,
        'show_percentages': False,
        'title': _title_modifier_defaults(title),
    }


def _axis_modifier_defaults(label: str) -> dict[str, Any]:
    return {
        'label_size': 12,
        'label': label,
        'hide_label': False,
        'tick_count': None,
        'tick_size': None,
        'show_grid_lines': True,
        'scale': 'lin',
    }


def _title_modifier_defaults(text: str) -> dict[str, Any]:
    return {
        'size': 14,
        'text': text,
        'hide_title': True,
        'position': 'top',
    }


def _histogram_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
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


def _scatter_plot_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
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


def _pie_chart_modifier_schema(default_modifiers: dict[str, Any]) -> list[dict[str, Any]]:
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


def _merge_modifier_defaults(base: dict[str, Any], overrides: dict[str, object] | None) -> dict[str, Any]:
    if overrides is None:
        return base
    return _merge_modifier_value(base, overrides)


def _merge_modifier_value(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        keys = set(base) | set(override)
        return {key: _merge_modifier_value(base.get(key), override.get(key)) for key in keys}
    return override if override is not None else base


def _dataframe_column_definitions(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            'id': str(column_name),
            'title': str(column_name),
            'data_type': str(dtype),
            'filter_kinds': _filter_kinds_for_dtype(dtype),
        }
        for column_name, dtype in dataframe.dtypes.items()
    ]


def _filter_kinds_for_dtype(dtype: Any) -> list[str]:
    if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_datetime64_any_dtype(dtype):
        return ['range', 'value']
    if pd.api.types.is_bool_dtype(dtype):
        return ['value']
    return ['value', 'regex']


def _json_safe_modifier_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return _json_safe_modifier_value(normalized)
    return str(value)
