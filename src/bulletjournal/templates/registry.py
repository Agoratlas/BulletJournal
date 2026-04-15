from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import cast

from bulletjournal.templates.builtin_provider import (
    BUILTIN_PROVIDER,
    builtin_notebook_assets,
    builtin_pipeline_assets,
    example_provider,
)
from bulletjournal.templates.provider import TemplateAsset, TemplateProvider


ENTRY_POINT_GROUP = 'bulletjournal.templates'


def discover_template_providers() -> list[TemplateProvider]:
    providers: list[object] = [example_provider(), *discover_external_template_providers()]
    return cast(list[TemplateProvider], providers)


def discover_external_template_providers() -> list[TemplateProvider]:
    providers: list[object] = []
    for entry_point in sorted(_template_entry_points(), key=lambda item: item.name):
        provider_factory = entry_point.load()
        provider = provider_factory()
        providers.append(provider)
    return cast(list[TemplateProvider], providers)


def builtin_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in builtin_notebook_assets() if asset.path is not None]


def builtin_pipeline_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in builtin_pipeline_assets() if asset.path is not None]


def example_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in example_provider().list_notebook_templates() if asset.path is not None]


def example_pipeline_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in example_provider().pipeline_templates() if asset.path is not None]


def default_notebook_assets() -> list[TemplateAsset]:
    return [*builtin_notebook_assets(), *example_provider().list_notebook_templates()]


def default_pipeline_assets() -> list[TemplateAsset]:
    return [*builtin_pipeline_assets(), *example_provider().pipeline_templates()]


def _template_entry_points() -> list[importlib.metadata.EntryPoint]:
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, 'select'):
        return list(entry_points.select(group=ENTRY_POINT_GROUP))
    return list(entry_points.get(ENTRY_POINT_GROUP, []))
