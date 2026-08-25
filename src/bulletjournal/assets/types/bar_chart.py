from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.category_ordering import (
    category_order_value_key,
    normalize_category_order_modifier_value,
    resolve_category_order,
    sort_category_rows,
    validate_category_order_value,
)
from bulletjournal.assets.prepare_utils import (
    coerce_filter_value,
    frame_with_filters,
    frame_with_sort,
    json_safe_value,
    prepared_table_payload,
    resolve_filters,
    resolve_highlights,
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
    validate_highlights,
    validate_modifier_defaults,
    validate_number,
    validate_title_modifier_defaults,
)
from bulletjournal.domain.errors import InvalidRequestError

DEFAULT_BAR_CHART_AGGREGATION = 'sum'
DEFAULT_BAR_CHART_CATEGORY_ORDER = 'category_asc'
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
    group: str | None
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
        group: str | None = None,
        **modifier_kwargs: Any,
    ) -> None:
        self.dataframe = dataframe
        self.category = category
        self.color = color
        self.value = value
        self.aggregation = aggregation
        self.group = group
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
        if self.group is not None:
            if not isinstance(self.group, str) or not self.group:
                raise TypeError('Bar chart assets require `group` to be a non-empty column name when provided.')
            if self.group not in self.dataframe.columns:
                raise ValueError(f'Bar chart group column `{self.group}` was not found in the provided DataFrame.')
        validate_pie_chart_color(self.dataframe, category_column=self.category, color=self.color, label='Bar chart')
        validate_bar_chart_aggregation(self.dataframe, value_column=self.value, aggregation=self.aggregation)
        validate_bar_chart_modifier_defaults(self.modifier_defaults)


def validate_bar_chart_modifier_defaults(value: dict[str, object] | None) -> None:
    validate_modifier_defaults(
        value,
        allowed_keys={
            'bar_width',
            'border_thickness',
            'category_order',
            'group_order',
            'group_mode',
            'group_normalize',
            'group_spacing',
            'x_axis',
            'y_axis',
            'title',
            'highlights',
        },
        context='Bar chart assets',
    )
    if value is None:
        return
    if 'bar_width' in value:
        validate_number(value['bar_width'], label='Bar chart modifier `bar_width`')
    if 'border_thickness' in value:
        validate_number(value['border_thickness'], label='Bar chart modifier `border_thickness`')
    if 'category_order' in value:
        validate_category_order_value(value['category_order'], label='Bar chart modifier `category_order`')
    if 'group_order' in value:
        validate_category_order_value(value['group_order'], label='Bar chart modifier `group_order`')
    if 'group_mode' in value and value['group_mode'] not in ('grouped', 'stacked'):
        raise TypeError('Bar chart modifier `group_mode` must be "grouped" or "stacked".')
    if 'group_normalize' in value and not isinstance(value['group_normalize'], bool):
        raise TypeError('Bar chart modifier `group_normalize` must be a bool.')
    if 'group_spacing' in value:
        validate_number(value['group_spacing'], label='Bar chart modifier `group_spacing`')
        if float(value['group_spacing']) < 0 or float(value['group_spacing']) > 50:
            raise TypeError('Bar chart modifier `group_spacing` must be between 0 and 50.')
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
        'category_order': DEFAULT_BAR_CHART_CATEGORY_ORDER,
        'group_order': DEFAULT_BAR_CHART_CATEGORY_ORDER,
        'group_mode': 'grouped',
        'group_normalize': False,
        'group_spacing': 10,
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
            'id': 'category_order',
            'title': 'Category order',
            'kind': 'value',
            'category': 'saved_view',
            'server_targets': ['main'],
            'default_value': default_modifiers['category_order'],
        },
        {
            'id': 'group_order',
            'title': 'Group order',
            'kind': 'value',
            'category': 'saved_view',
            'server_targets': ['main'],
            'default_value': default_modifiers['group_order'],
        },
        {
            'id': 'group_mode',
            'title': 'Group mode',
            'kind': 'value',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['group_mode'],
        },
        {
            'id': 'group_normalize',
            'title': 'Normalize groups',
            'kind': 'bool',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['group_normalize'],
        },
        {
            'id': 'group_spacing',
            'title': 'Group spacing',
            'kind': 'float',
            'category': 'saved_view',
            'server_targets': [],
            'default_value': default_modifiers['group_spacing'],
            'min_value': 0,
            'max_value': 50,
            'step': 1,
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
    if asset.modifier_defaults and 'highlights' in asset.modifier_defaults:
        validate_highlights(asset.modifier_defaults['highlights'], dataframe=asset.dataframe)
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    normalized_aggregation = normalize_bar_chart_aggregation(asset.aggregation)
    if normalized_aggregation is None:
        raise InvalidRequestError('Bar chart asset definition is missing its aggregation mode.')
    has_group = asset.group is not None
    color_target_column = str(asset.group) if has_group else str(asset.category)
    color_mapping = [
        {'value': json_safe_modifier_value(category_value), 'color': color_value}
        for category_value, color_value in normalize_pie_chart_color_mapping(
            asset.dataframe,
            category_column=color_target_column,
            color=asset.color,
        )
    ]
    default_modifiers = {
        'page': {'index': 0, 'size': 10},
        'sort': [],
        'filters': [],
        'highlights': [],
        **merge_nested_dicts(
            bar_chart_modifier_defaults(
                title=title,
                category_column=str(asset.category),
                y_axis_label=bar_chart_y_axis_label(str(asset.value), normalized_aggregation),
            ),
            asset.modifier_defaults,
        ),
    }
    default_modifiers['category_order'] = normalize_category_order_modifier_value(
        default_modifiers.get('category_order', DEFAULT_BAR_CHART_CATEGORY_ORDER),
        label='Bar chart modifier `category_order`',
    )
    default_modifiers['group_order'] = normalize_category_order_modifier_value(
        default_modifiers.get('group_order', DEFAULT_BAR_CHART_CATEGORY_ORDER),
        label='Bar chart modifier `group_order`',
    )
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
    definition: dict[str, Any] = {
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
        'bar_category_column': str(asset.category),
        'bar_value_column': str(asset.value),
        'bar_aggregation': normalized_aggregation,
        'bar_color_mapping': color_mapping,
        'bar_default_color': DEFAULT_PIE_CHART_COLOR,
    }
    if has_group:
        if asset.group is not None:
            definition['bar_group_column'] = str(asset.group)
    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=True,
        definition=definition,
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
    group_column = definition.get('bar_group_column')
    if group_column is not None and group_column not in column_id_map:
        raise InvalidRequestError('Bar chart asset definition references an unknown group column.')
    aggregation = normalize_bar_chart_aggregation(definition.get('bar_aggregation'))
    if aggregation is None:
        raise InvalidRequestError('Bar chart asset definition is missing its aggregation mode.')
    category_dtype = schema[column_id_map[category_column]]
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_highlights = resolve_highlights(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_category_order = resolve_category_order(
        default_modifiers,
        modifier_overrides,
        default_mode=DEFAULT_BAR_CHART_CATEGORY_ORDER,
        column=category_column,
        dtype=category_dtype,
    )
    filtered_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    selected_categories = resolve_bar_chart_selected_categories(
        transient_modifiers,
        column=category_column,
        dtype=category_dtype,
    )
    table_frame = filtered_frame
    if selected_categories:
        table_frame = apply_bar_chart_selection(table_frame, category_column, selected_categories, column_id_map)
    if group_column is not None and group_column in column_id_map:
        selected_groups = resolve_bar_chart_selected_groups(
            transient_modifiers,
            column=group_column,
            dtype=schema[column_id_map[group_column]],
        )
        if selected_groups:
            table_frame = apply_bar_chart_group_selection(table_frame, group_column, selected_groups, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
    return {
        'main': prepare_bar_chart_main_payload(
            filtered_frame,
            category_column=category_column,
            value_column=value_column,
            aggregation=aggregation,
            column_id_map=column_id_map,
            color_mapping_entries=definition.get('bar_color_mapping'),
            category_order=resolved_category_order,
            default_color=definition.get('bar_default_color'),
            group_column=group_column,
            group_order=resolve_category_order(
                default_modifiers,
                modifier_overrides,
                default_mode=DEFAULT_BAR_CHART_CATEGORY_ORDER,
                column=group_column,
                dtype=schema[column_id_map[group_column]]
                if group_column and group_column in column_id_map
                else pl.Utf8,
            )
            if group_column
            else None,
        ),
        'table': prepared_table_payload(
            table_frame,
            schema=schema,
            column_names=column_names,
            resolved_page=resolved_page,
            resolved_sort=resolved_sort,
            resolved_highlights=resolved_highlights,
            column_id_map=column_id_map,
        ),
    }, {
        'page': resolved_page,
        'sort': resolved_sort,
        'filters': resolved_filters,
        'highlights': resolved_highlights,
    }


def prepare_bar_chart_main_payload(
    frame: pl.LazyFrame,
    *,
    category_column: str,
    value_column: str,
    aggregation: str,
    column_id_map: dict[str, Any],
    color_mapping_entries: object,
    category_order: str | list[Any],
    default_color: object,
    group_column: str | None = None,
    group_order: str | list[Any] | None = None,
) -> dict[str, Any]:
    category_name = column_id_map[category_column]
    value_name = column_id_map[value_column]
    has_group = group_column is not None and group_column in column_id_map
    group_name = column_id_map[group_column] if has_group else None
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
        payload: dict[str, Any] = {
            'kind': 'bar_chart',
            'category_column': category_column,
            'value_column': value_column,
            'aggregation': aggregation,
            'rows_total': rows_total,
            'non_null_rows': 0,
            'bars': [],
        }
        if has_group:
            payload['group_column'] = group_column
        return payload

    filtered = frame.filter(pl.col(category_name).is_not_null() & pl.col(value_name).is_not_null())

    if has_group and group_name is not None:
        filtered = filtered.filter(pl.col(group_name).is_not_null())
        aggregated = (
            filtered.group_by([category_name, group_name])
            .agg(bar_chart_aggregation_expr(value_name, aggregation))
            .collect()
        )

        aggregated_rows = aggregated.to_dicts()

        category_grouped: dict[str, list[dict[str, Any]]] = {}
        all_group_values: dict[str, dict[str, Any]] = {}
        for row in aggregated_rows:
            cat_value = json_safe_value(row.get(category_name))
            grp_value = json_safe_value(row.get(group_name))
            agg_value = json_safe_value(row.get('aggregate_value'))
            if cat_value is None or grp_value is None or agg_value is None:
                continue
            cat_key = category_order_value_key(cat_value)
            if cat_key not in category_grouped:
                category_grouped[cat_key] = []
            category_grouped[cat_key].append(
                {
                    'cat_value': cat_value,
                    'grp_value': grp_value,
                    'grp_label': grp_value if isinstance(grp_value, str) else str(grp_value),
                    'aggregate_value': agg_value,
                }
            )
            gk = category_order_value_key(grp_value)
            if gk not in all_group_values:
                all_group_values[gk] = {
                    'grp_value': grp_value,
                    'grp_label': grp_value if isinstance(grp_value, str) else str(grp_value),
                }

        if not category_grouped:
            payload: dict[str, Any] = {
                'kind': 'bar_chart',
                'category_column': category_column,
                'value_column': value_column,
                'aggregation': aggregation,
                'rows_total': rows_total,
                'non_null_rows': 0,
                'bars': [],
            }
            if has_group:
                payload['group_column'] = group_column
            return payload

        # Sort categories by category_order
        cat_entries = [
            {'cat_key': key, 'cat_value': rows[0]['cat_value'], 'rows': rows} for key, rows in category_grouped.items()
        ]
        sorted_cat_entries = sort_category_rows(
            cat_entries,
            category_field='cat_key',
            value_field='aggregate_value',
            default_mode=DEFAULT_BAR_CHART_CATEGORY_ORDER,
            category_order=category_order,
        )

        # Sort all unique groups globally
        sorted_all_groups = sort_category_rows(
            list(all_group_values.values()),
            category_field='grp_value',
            value_field='aggregate_value',
            default_mode=DEFAULT_BAR_CHART_CATEGORY_ORDER,
            category_order=group_order or DEFAULT_BAR_CHART_CATEGORY_ORDER,
        )

        # Assign stable indices to sorted groups
        group_stable_index: dict[str, int] = {}
        for idx, grp in enumerate(sorted_all_groups):
            gk = category_order_value_key(grp['grp_value'])
            group_stable_index[gk] = idx

        # Build one bar per (category x group) combination — fill missing combos with zero
        bars: list[dict[str, Any]] = []
        for cat_idx, entry in enumerate(sorted_cat_entries):
            cat_value = entry['cat_value']
            cat_rows = entry['rows']
            cat_total = sum(row['aggregate_value'] for row in cat_rows)
            existing_by_group: dict[str, dict[str, Any]] = {}
            for row in cat_rows:
                gk = category_order_value_key(row['grp_value'])
                existing_by_group[gk] = row
            for grp_idx, grp_entry in enumerate(sorted_all_groups):
                gk = category_order_value_key(grp_entry['grp_value'])
                existing_row = existing_by_group.get(gk)
                if existing_row:
                    agg_value = existing_row['aggregate_value']
                    proportion = float(agg_value) / float(cat_total) if cat_total else 0
                    bars.append(
                        {
                            'value': cat_value,
                            'label': cat_value if isinstance(cat_value, str) else str(cat_value),
                            'group': existing_row['grp_value'],
                            'group_label': existing_row['grp_label'],
                            'aggregate_value': agg_value,
                            'group_proportion': proportion,
                            'category_index': cat_idx,
                            'group_index': grp_idx,
                            'color': pie_chart_color_for_value(
                                existing_row['grp_value'],
                                index=grp_idx,
                                explicit_color_mapping=explicit_color_mapping,
                                default_color=resolved_default_color,
                            ),
                        }
                    )
                else:
                    bars.append(
                        {
                            'value': cat_value,
                            'label': cat_value if isinstance(cat_value, str) else str(cat_value),
                            'group': grp_entry['grp_value'],
                            'group_label': grp_entry['grp_label'],
                            'aggregate_value': 0,
                            'group_proportion': 0,
                            'category_index': cat_idx,
                            'group_index': grp_idx,
                            'color': pie_chart_color_for_value(
                                grp_entry['grp_value'],
                                index=grp_idx,
                                explicit_color_mapping=explicit_color_mapping,
                                default_color=resolved_default_color,
                            ),
                        }
                    )
    else:
        aggregated = filtered.group_by(category_name).agg(bar_chart_aggregation_expr(value_name, aggregation)).collect()
        bars = []
        grouped_rows = sort_category_rows(
            aggregated.to_dicts(),
            category_field=category_name,
            value_field='aggregate_value',
            default_mode=DEFAULT_BAR_CHART_CATEGORY_ORDER,
            category_order=category_order,
        )
        for cat_idx, row in enumerate(grouped_rows):
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
                    'category_index': cat_idx,
                    'color': pie_chart_color_for_value(
                        value,
                        index=len(bars),
                        explicit_color_mapping=explicit_color_mapping,
                        default_color=resolved_default_color,
                    ),
                }
            )

    payload = {
        'kind': 'bar_chart',
        'category_column': category_column,
        'value_column': value_column,
        'aggregation': aggregation,
        'rows_total': rows_total,
        'non_null_rows': non_null_rows,
        'bars': bars,
    }
    if has_group and group_column is not None:
        payload['group_column'] = group_column
    return payload


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


def resolve_bar_chart_selected_groups(
    transient_modifiers: dict[str, Any],
    *,
    column: str,
    dtype: pl.DataType,
) -> list[Any]:
    candidate = transient_modifiers.get('selected_groups') if isinstance(transient_modifiers, dict) else None
    if candidate in (None, []):
        return []
    if not isinstance(candidate, list):
        raise InvalidRequestError('transient_modifiers.selected_groups must be an array.')
    resolved: list[Any] = []
    seen_keys: set[str] = set()
    for value in candidate:
        coerced = coerce_filter_value(dtype, value, column=column, kind='selected_groups')
        if coerced is None:
            raise InvalidRequestError('transient_modifiers.selected_groups cannot contain null values.')
        value_key = json.dumps(json_safe_value(coerced), ensure_ascii=True, sort_keys=True)
        if value_key in seen_keys:
            continue
        seen_keys.add(value_key)
        resolved.append(coerced)
    return resolved


def apply_bar_chart_group_selection(
    frame: pl.LazyFrame,
    column: str,
    selected_groups: list[Any],
    column_id_map: dict[str, Any],
) -> pl.LazyFrame:
    column_name = column_id_map[column]
    return frame.filter(pl.col(column_name).is_not_null() & pl.col(column_name).is_in(selected_groups).fill_null(False))
