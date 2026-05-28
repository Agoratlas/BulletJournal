from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd


@dataclass(slots=True)
class BaseAsset:
    asset_type_id: ClassVar[str] = 'generic'
    interactive: ClassVar[bool] = False


@dataclass(slots=True)
class MarkdownAsset(BaseAsset):
    text: str

    asset_type_id: ClassVar[str] = 'markdown'
    interactive: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError('Markdown assets require a string payload.')


@dataclass(slots=True)
class DataFrameAsset(BaseAsset):
    dataframe: pd.DataFrame

    asset_type_id: ClassVar[str] = 'dataframe'
    interactive: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('DataFrame assets require a pandas.DataFrame payload.')


@dataclass(slots=True)
class HistogramAsset(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    bins: int = 20
    shape: str | None = None
    size: str | None = None
    color: str | None = None
    modifier_defaults: dict[str, object] | None = None

    asset_type_id: ClassVar[str] = 'histogram'
    interactive: ClassVar[bool] = True

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
        _validate_optional_asset_column(self.dataframe, self.shape, label='Histogram `shape`')
        _validate_optional_asset_column(self.dataframe, self.size, label='Histogram `size`')
        _validate_optional_asset_column(self.dataframe, self.color, label='Histogram `color`')
        _validate_histogram_modifier_defaults(self.modifier_defaults)


@dataclass(slots=True)
class PieChartAsset(BaseAsset):
    dataframe: pd.DataFrame
    category: str
    color: str | dict[object, str] | None = None
    modifier_defaults: dict[str, object] | None = None

    asset_type_id: ClassVar[str] = 'pie_chart'
    interactive: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Pie chart assets require a pandas.DataFrame payload.')
        if not isinstance(self.category, str) or not self.category:
            raise TypeError('Pie chart assets require `category` to be a non-empty column name.')
        if self.category not in self.dataframe.columns:
            raise ValueError(f'Pie chart category column `{self.category}` was not found in the provided DataFrame.')
        _validate_pie_chart_color(self.dataframe, category_column=self.category, color=self.color)
        _validate_pie_chart_modifier_defaults(self.modifier_defaults)


@dataclass(slots=True)
class ScatterPlotAsset(BaseAsset):
    dataframe: pd.DataFrame
    x: str
    y: str
    shape: str | None = None
    size: str | None = None
    color: str | None = None
    modifier_defaults: dict[str, object] | None = None

    asset_type_id: ClassVar[str] = 'scatter_plot'
    interactive: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('Scatter plot assets require a pandas.DataFrame payload.')
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
        _validate_optional_asset_column(self.dataframe, self.shape, label='Scatter plot `shape`')
        _validate_optional_asset_column(self.dataframe, self.size, label='Scatter plot `size`')
        _validate_optional_asset_column(self.dataframe, self.color, label='Scatter plot `color`')
        _validate_scatter_plot_modifier_defaults(self.modifier_defaults)


def _validate_optional_asset_column(dataframe: pd.DataFrame, column: str | None, *, label: str) -> None:
    if column is None:
        return
    if not isinstance(column, str) or not column:
        raise TypeError(f'{label} must be a non-empty column name when provided.')
    if column not in dataframe.columns:
        raise ValueError(f'{label} column `{column}` was not found in the provided DataFrame.')


def _validate_histogram_modifier_defaults(value: dict[str, object] | None) -> None:
    _validate_modifier_defaults(
        value,
        allowed_keys={'bin_count', 'bar_width', 'border_thickness', 'x_axis', 'y_axis', 'title'},
        context='Histogram assets',
    )
    if value is None:
        return
    if 'bin_count' in value:
        _validate_positive_int(value['bin_count'], label='Histogram modifier `bin_count`')
    if 'bar_width' in value:
        _validate_number(value['bar_width'], label='Histogram modifier `bar_width`')
    if 'border_thickness' in value:
        _validate_number(value['border_thickness'], label='Histogram modifier `border_thickness`')
    if 'x_axis' in value:
        _validate_axis_modifier_defaults(value['x_axis'], label='Histogram modifier `x_axis`')
    if 'y_axis' in value:
        _validate_axis_modifier_defaults(value['y_axis'], label='Histogram modifier `y_axis`')
    if 'title' in value:
        _validate_title_modifier_defaults(value['title'], label='Histogram modifier `title`')


def _validate_scatter_plot_modifier_defaults(value: dict[str, object] | None) -> None:
    _validate_modifier_defaults(
        value,
        allowed_keys={'min_point_size', 'max_point_size', 'show_legend', 'shape_style', 'x_axis', 'y_axis', 'title'},
        context='Scatter plot assets',
    )
    if value is None:
        return
    if 'min_point_size' in value:
        _validate_number(value['min_point_size'], label='Scatter plot modifier `min_point_size`')
    if 'max_point_size' in value:
        _validate_number(value['max_point_size'], label='Scatter plot modifier `max_point_size`')
    if 'show_legend' in value and not isinstance(value['show_legend'], bool):
        raise TypeError('Scatter plot modifier `show_legend` must be a bool.')
    if 'shape_style' in value and value['shape_style'] not in {'outline', 'filled'}:
        raise TypeError('Scatter plot modifier `shape_style` must be `outline` or `filled`.')
    if 'x_axis' in value:
        _validate_axis_modifier_defaults(value['x_axis'], label='Scatter plot modifier `x_axis`')
    if 'y_axis' in value:
        _validate_axis_modifier_defaults(value['y_axis'], label='Scatter plot modifier `y_axis`')
    if 'title' in value:
        _validate_title_modifier_defaults(value['title'], label='Scatter plot modifier `title`')


def _validate_pie_chart_modifier_defaults(value: dict[str, object] | None) -> None:
    _validate_modifier_defaults(
        value,
        allowed_keys={
            'inner_radius',
            'label_size',
            'label_threshold',
            'label_position',
            'merge_threshold',
            'border_thickness',
            'merged_category_label',
            'show_merged_category',
            'show_percentages',
            'title',
        },
        context='Pie chart assets',
    )
    if value is None:
        return
    if 'inner_radius' in value:
        _validate_number(value['inner_radius'], label='Pie chart modifier `inner_radius`')
        if float(value['inner_radius']) < 0 or float(value['inner_radius']) > 1:
            raise TypeError('Pie chart modifier `inner_radius` must be between 0 and 1.')
    if 'label_size' in value:
        _validate_number(value['label_size'], label='Pie chart modifier `label_size`')
        if float(value['label_size']) < 1:
            raise TypeError('Pie chart modifier `label_size` must be positive.')
    if 'label_threshold' in value:
        _validate_number(value['label_threshold'], label='Pie chart modifier `label_threshold`')
        if float(value['label_threshold']) < 0 or float(value['label_threshold']) > 100:
            raise TypeError('Pie chart modifier `label_threshold` must be between 0 and 100.')
    if 'label_position' in value:
        _validate_number(value['label_position'], label='Pie chart modifier `label_position`')
        if float(value['label_position']) < 0 or float(value['label_position']) > 200:
            raise TypeError('Pie chart modifier `label_position` must be between 0 and 200.')
    if 'merge_threshold' in value:
        _validate_number(value['merge_threshold'], label='Pie chart modifier `merge_threshold`')
        if float(value['merge_threshold']) < 0 or float(value['merge_threshold']) > 100:
            raise TypeError('Pie chart modifier `merge_threshold` must be between 0 and 100.')
    if 'border_thickness' in value:
        _validate_number(value['border_thickness'], label='Pie chart modifier `border_thickness`')
        if float(value['border_thickness']) < 0:
            raise TypeError('Pie chart modifier `border_thickness` must be non-negative.')
    if 'merged_category_label' in value and not isinstance(value['merged_category_label'], str):
        raise TypeError('Pie chart modifier `merged_category_label` must be a string.')
    if 'show_merged_category' in value and not isinstance(value['show_merged_category'], bool):
        raise TypeError('Pie chart modifier `show_merged_category` must be a bool.')
    if 'show_percentages' in value and not isinstance(value['show_percentages'], bool):
        raise TypeError('Pie chart modifier `show_percentages` must be a bool.')
    if 'title' in value:
        _validate_title_modifier_defaults(value['title'], label='Pie chart modifier `title`')


def _validate_pie_chart_color(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color: str | dict[object, str] | None,
) -> None:
    if color is None:
        return
    if isinstance(color, str):
        _validate_optional_asset_column(dataframe, color, label='Pie chart `color`')
        _validate_pie_chart_color_column(dataframe, category_column=category_column, color_column=color)
        return
    if not isinstance(color, dict):
        raise TypeError('Pie chart `color` must be a column name or a dict mapping categories to colors.')
    for key, value in color.items():
        if key is None:
            raise TypeError('Pie chart `color` mapping keys cannot be None.')
        if not isinstance(value, str) or not value.strip():
            raise TypeError('Pie chart `color` mapping values must be non-empty strings.')


def _validate_pie_chart_color_column(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color_column: str,
) -> None:
    color_pairs = dataframe[[category_column, color_column]]
    for category_value, group in color_pairs.groupby(category_column, dropna=True):
        distinct_colors = {value.item() if hasattr(value, 'item') else value for value in group[color_column].tolist()}
        if any(pd.isna(value) for value in distinct_colors):
            raise ValueError(
                f'Pie chart color column `{color_column}` contains missing colors for category `{category_value}`.'
            )
        if len(distinct_colors) > 1:
            raise ValueError(
                f'Pie chart color column `{color_column}` assigns multiple colors to category `{category_value}`.'
            )
        only_color = next(iter(distinct_colors), None)
        if not isinstance(only_color, str) or not only_color.strip():
            raise TypeError(
                'Pie chart color column '
                f'`{color_column}` must provide non-empty string colors '
                f'for category `{category_value}`.'
            )


def normalize_pie_chart_color_mapping(
    dataframe: pd.DataFrame,
    *,
    category_column: str,
    color: str | dict[object, str] | None,
) -> list[tuple[Any, str]]:
    if color is None:
        return []
    if isinstance(color, dict):
        return [(_normalize_pie_chart_value(key), value.strip()) for key, value in color.items()]
    color_pairs = dataframe[[category_column, color]].dropna(subset=[category_column])
    mapping: list[tuple[Any, str]] = []
    for category_value, group in color_pairs.groupby(category_column, dropna=True):
        distinct_colors = [value.item() if hasattr(value, 'item') else value for value in pd.unique(group[color])]
        only_color = distinct_colors[0] if distinct_colors else None
        if isinstance(only_color, str) and only_color.strip():
            mapping.append((_normalize_pie_chart_value(category_value), only_color.strip()))
    return mapping


def _normalize_pie_chart_value(value: Any) -> Any:
    return value.item() if hasattr(value, 'item') else value


def _validate_modifier_defaults(value: dict[str, object] | None, *, allowed_keys: set[str], context: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise TypeError(f'{context} modifier defaults must be provided as a dict when set.')
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{context} received unsupported modifier defaults: {joined}.')


def _validate_axis_modifier_defaults(value: object, *, label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f'{label} must be a dict.')
    allowed_keys = {'label_size', 'label', 'hide_label', 'tick_count', 'tick_size', 'show_grid_lines', 'scale'}
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{label} received unsupported keys: {joined}.')
    if 'label_size' in value:
        _validate_number_or_none(value['label_size'], label=f'{label}.label_size')
    if 'label' in value and not isinstance(value['label'], str):
        raise TypeError(f'{label}.label must be a string.')
    if 'hide_label' in value and not isinstance(value['hide_label'], bool):
        raise TypeError(f'{label}.hide_label must be a bool.')
    if 'tick_count' in value:
        _validate_int_or_none(value['tick_count'], label=f'{label}.tick_count')
    if 'tick_size' in value:
        _validate_number_or_none(value['tick_size'], label=f'{label}.tick_size')
    if 'show_grid_lines' in value and not isinstance(value['show_grid_lines'], bool):
        raise TypeError(f'{label}.show_grid_lines must be a bool.')
    if 'scale' in value and value['scale'] not in {'lin', 'log'}:
        raise TypeError(f'{label}.scale must be `lin` or `log`.')


def _validate_title_modifier_defaults(value: object, *, label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f'{label} must be a dict.')
    allowed_keys = {'size', 'text', 'hide_title', 'position'}
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        joined = ', '.join(unknown)
        raise TypeError(f'{label} received unsupported keys: {joined}.')
    if 'size' in value:
        _validate_number_or_none(value['size'], label=f'{label}.size')
    if 'text' in value and not isinstance(value['text'], str):
        raise TypeError(f'{label}.text must be a string.')
    if 'hide_title' in value and not isinstance(value['hide_title'], bool):
        raise TypeError(f'{label}.hide_title must be a bool.')
    if 'position' in value and value['position'] not in {'top', 'bottom'}:
        raise TypeError(f'{label}.position must be `top` or `bottom`.')


def _validate_positive_int(value: object, *, label: str) -> None:
    if not isinstance(value, int) or value < 1:
        raise TypeError(f'{label} must be a positive integer.')


def _validate_int_or_none(value: object, *, label: str) -> None:
    if value is not None and not isinstance(value, int):
        raise TypeError(f'{label} must be an integer or None.')


def _validate_number(value: object, *, label: str) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f'{label} must be a number.')


def _validate_number_or_none(value: object, *, label: str) -> None:
    if value is not None:
        _validate_number(value, label=label)


def asset_type_id_for_class(asset_type: object) -> str | None:
    if not isinstance(asset_type, type) or not issubclass(asset_type, BaseAsset):
        return None
    candidate = getattr(asset_type, 'asset_type_id', None)
    return candidate if isinstance(candidate, str) and candidate else None


def asset_type_id_for_instance(asset: object) -> str | None:
    if not isinstance(asset, BaseAsset):
        return None
    return asset_type_id_for_class(asset.__class__)
