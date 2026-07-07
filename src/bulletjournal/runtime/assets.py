from __future__ import annotations

from typing import Any

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.types.bar_chart import BarChart
from bulletjournal.assets.types.collection import Collection
from bulletjournal.assets.types.dataframe import DataFrame
from bulletjournal.assets.types.histogram import Histogram
from bulletjournal.assets.types.iframe import Iframe
from bulletjournal.assets.types.markdown import Markdown
from bulletjournal.assets.types.pie_chart import PieChart
from bulletjournal.assets.types.scatter_plot import ScatterPlot
from bulletjournal.domain.artifact_names import invalid_artifact_name_message, is_valid_artifact_name
from bulletjournal.runtime.context import current_runtime_context


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
    if not is_valid_artifact_name(name):
        raise ValueError(invalid_artifact_name_message(name))
    context = current_runtime_context()
    context.finalize_asset_push(
        asset=asset,
        name=name,
        title=title,
        description=description,
        asset_type=asset_type,
    )


__all__ = [
    'BarChart',
    'BaseAsset',
    'Collection',
    'DataFrame',
    'Histogram',
    'Iframe',
    'Markdown',
    'PieChart',
    'ScatterPlot',
    'push',
]
