from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


ENTRY_POINT_GROUP = 'bulletjournal.templates'
BUILTIN_PROVIDER = 'builtin'


@dataclass(slots=True, frozen=True)
class TemplateAsset:
    provider: str
    kind: str
    name: str
    file_name: str
    ref: str
    path: Path
    origin_revision: str


class TemplateProvider(Protocol):
    provider_name: str

    def notebook_templates(self) -> list[TemplateAsset]: ...

    def pipeline_templates(self) -> list[TemplateAsset]: ...


@dataclass(slots=True)
class FilesystemTemplateProvider:
    provider_name: str
    notebook_root: Path
    pipeline_root: Path
    origin_revision: str

    def notebook_templates(self) -> list[TemplateAsset]:
        templates: list[TemplateAsset] = []
        for path in sorted(self.notebook_root.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            name = path.relative_to(self.notebook_root).as_posix()
            logical_name = path.relative_to(self.notebook_root).with_suffix('').as_posix()
            templates.append(
                TemplateAsset(
                    provider=self.provider_name,
                    kind='notebook',
                    name=logical_name,
                    file_name=name,
                    ref=f'{self.provider_name}/{logical_name}',
                    path=path,
                    origin_revision=self.origin_revision,
                )
            )
        return templates

    def pipeline_templates(self) -> list[TemplateAsset]:
        templates: list[TemplateAsset] = []
        for path in sorted(self.pipeline_root.rglob('*.json')):
            if '__pycache__' in path.parts:
                continue
            name = path.relative_to(self.pipeline_root).as_posix()
            logical_name = path.relative_to(self.pipeline_root).with_suffix('').as_posix()
            templates.append(
                TemplateAsset(
                    provider=self.provider_name,
                    kind='pipeline',
                    name=logical_name,
                    file_name=name,
                    ref=f'{self.provider_name}/{logical_name}',
                    path=path,
                    origin_revision=self.origin_revision,
                )
            )
        return templates


def builtin_provider() -> FilesystemTemplateProvider:
    templates_root = Path(__file__).resolve().parent
    return FilesystemTemplateProvider(
        provider_name=BUILTIN_PROVIDER,
        notebook_root=templates_root / 'builtin',
        pipeline_root=templates_root / 'pipelines',
        origin_revision='builtin@0.1.0',
    )


def discover_template_providers() -> list[TemplateProvider]:
    providers: list[TemplateProvider] = [builtin_provider()]
    for entry_point in sorted(_template_entry_points(), key=lambda item: item.name):
        provider_factory = entry_point.load()
        provider = provider_factory()
        providers.append(provider)
    return providers


def builtin_templates() -> list[Path]:
    return [asset.path for asset in builtin_provider().notebook_templates()]


def builtin_pipeline_templates() -> list[Path]:
    return [asset.path for asset in builtin_provider().pipeline_templates()]


def _template_entry_points() -> list[importlib.metadata.EntryPoint]:
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, 'select'):
        return list(entry_points.select(group=ENTRY_POINT_GROUP))
    return list(entry_points.get(ENTRY_POINT_GROUP, []))
