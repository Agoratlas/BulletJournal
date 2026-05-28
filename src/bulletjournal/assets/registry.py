from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.serialization import SerializedAssetVersion
from bulletjournal.assets.types.bar_chart import BarChart, prepare_bar_chart, serialize_bar_chart
from bulletjournal.assets.types.dataframe import DataFrame, prepare_dataframe, serialize_dataframe
from bulletjournal.assets.types.histogram import Histogram, prepare_histogram, serialize_histogram
from bulletjournal.assets.types.markdown import Markdown, serialize_markdown
from bulletjournal.assets.types.pie_chart import PieChart, prepare_pie_chart, serialize_pie_chart
from bulletjournal.assets.types.scatter_plot import ScatterPlot, prepare_scatter_plot, serialize_scatter_plot
from bulletjournal.assets.types.time_histogram import TimeHistogram, prepare_time_histogram, serialize_time_histogram

SerializeAssetFn = Callable[..., SerializedAssetVersion]
PrepareAssetFn = Callable[..., tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AssetRegistration:
    asset_type_id: str
    public_class_name: str
    asset_class: type[BaseAsset]
    serialize: SerializeAssetFn
    prepare: PrepareAssetFn | None = None


_REGISTRATIONS = (
    AssetRegistration('markdown', 'Markdown', Markdown, serialize_markdown),
    AssetRegistration('dataframe', 'DataFrame', DataFrame, serialize_dataframe, prepare_dataframe),
    AssetRegistration('bar_chart', 'BarChart', BarChart, serialize_bar_chart, prepare_bar_chart),
    AssetRegistration(
        'time_histogram', 'TimeHistogram', TimeHistogram, serialize_time_histogram, prepare_time_histogram
    ),
    AssetRegistration('histogram', 'Histogram', Histogram, serialize_histogram, prepare_histogram),
    AssetRegistration('pie_chart', 'PieChart', PieChart, serialize_pie_chart, prepare_pie_chart),
    AssetRegistration('scatter_plot', 'ScatterPlot', ScatterPlot, serialize_scatter_plot, prepare_scatter_plot),
)
_BY_TYPE_ID = {registration.asset_type_id: registration for registration in _REGISTRATIONS}
_BY_PUBLIC_CLASS_NAME = {registration.public_class_name: registration for registration in _REGISTRATIONS}


def asset_registration_for_type_id(asset_type_id: str | None) -> AssetRegistration | None:
    if not isinstance(asset_type_id, str) or not asset_type_id:
        return None
    return _BY_TYPE_ID.get(asset_type_id)


def asset_registration_for_class(asset_class: object) -> AssetRegistration | None:
    if not isinstance(asset_class, type) or not issubclass(asset_class, BaseAsset):
        return None
    for registration in _REGISTRATIONS:
        if asset_class is registration.asset_class:
            return registration
    for registration in _REGISTRATIONS:
        if issubclass(asset_class, registration.asset_class):
            return registration
    return None


def asset_registration_for_instance(asset: object) -> AssetRegistration | None:
    if not isinstance(asset, BaseAsset):
        return None
    return asset_registration_for_class(asset.__class__)


def asset_type_ids_by_public_class_name() -> dict[str, str]:
    return {registration.public_class_name: registration.asset_type_id for registration in _REGISTRATIONS}


def serialize_asset(asset: BaseAsset, *, object_store, title: str, description: str | None) -> SerializedAssetVersion:
    registration = asset_registration_for_instance(asset)
    if registration is None:
        raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')
    return registration.serialize(asset, object_store=object_store, title=title, description=description)
