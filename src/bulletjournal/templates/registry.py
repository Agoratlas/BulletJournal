from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import cast

from bulletjournal.templates.builtin_provider import BUILTIN_PROVIDER, builtin_provider
from bulletjournal.templates.provider import TemplateProvider


ENTRY_POINT_GROUP = 'bulletjournal.templates'


def discover_template_providers() -> list[TemplateProvider]:
    providers: list[object] = [builtin_provider()]
    for entry_point in sorted(_template_entry_points(), key=lambda item: item.name):
        provider_factory = entry_point.load()
        provider = provider_factory()
        providers.append(provider)
    return cast(list[TemplateProvider], providers)


def builtin_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in builtin_provider().list_notebook_templates() if asset.path is not None]


def builtin_pipeline_templates() -> list[Path]:
    return [cast(Path, asset.path) for asset in builtin_provider().pipeline_templates() if asset.path is not None]


def _template_entry_points() -> list[importlib.metadata.EntryPoint]:
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, 'select'):
        return list(entry_points.select(group=ENTRY_POINT_GROUP))
    return list(entry_points.get(ENTRY_POINT_GROUP, []))
