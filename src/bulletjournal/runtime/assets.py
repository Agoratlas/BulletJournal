from __future__ import annotations

from typing import Any

from bulletjournal.assets.runtime_types import (
    BaseAsset,
    DataFrameAsset,
    HistogramAsset,
    MarkdownAsset,
    PieChartAsset,
    ScatterPlotAsset,
)
from bulletjournal.runtime.context import current_runtime_context


class Markdown(MarkdownAsset):
    pass


class DataFrame(DataFrameAsset):
    pass


class Histogram(HistogramAsset):
    def __init__(
        self,
        dataframe,
        *,
        x,
        bins: int = 20,
        shape=None,
        size=None,
        color=None,
        **modifier_kwargs: Any,
    ) -> None:
        if 'bin_count' in modifier_kwargs:
            bin_count = modifier_kwargs.pop('bin_count')
            if not isinstance(bin_count, int):
                raise TypeError('Histogram modifier `bin_count` must be an int.')
            if bins != 20 and bins != bin_count:
                raise TypeError('Histogram `bins` and modifier `bin_count` must match when both are provided.')
            bins = bin_count
        super().__init__(
            dataframe=dataframe,
            x=x,
            bins=bins,
            shape=shape,
            size=size,
            color=color,
            modifier_defaults=modifier_kwargs or None,
        )


class PieChart(PieChartAsset):
    def __init__(
        self,
        dataframe,
        *,
        category,
        color=None,
        **modifier_kwargs: Any,
    ) -> None:
        super().__init__(
            dataframe=dataframe,
            category=category,
            color=color,
            modifier_defaults=modifier_kwargs or None,
        )


class ScatterPlot(ScatterPlotAsset):
    def __init__(
        self,
        dataframe,
        *,
        x,
        y,
        shape=None,
        size=None,
        color=None,
        **modifier_kwargs: Any,
    ) -> None:
        super().__init__(
            dataframe=dataframe,
            x=x,
            y=y,
            shape=shape,
            size=size,
            color=color,
            modifier_defaults=modifier_kwargs or None,
        )


def push(
    asset: BaseAsset,
    *,
    name: str,
    title: str,
    description: str | None = None,
    asset_type: type[BaseAsset] | None = None,
    **kwargs: Any,
) -> None:
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected asset push kwargs: {unexpected}')
    context = current_runtime_context()
    context.finalize_asset_push(
        asset=asset,
        name=name,
        title=title,
        description=description,
        asset_type=asset_type,
    )


__all__ = ['BaseAsset', 'DataFrame', 'Histogram', 'Markdown', 'PieChart', 'ScatterPlot', 'push']
