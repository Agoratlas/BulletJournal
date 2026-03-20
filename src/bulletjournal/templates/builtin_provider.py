from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bulletjournal.templates.provider import TemplateAsset


BUILTIN_PROVIDER = 'builtin'


@dataclass(slots=True)
class FilesystemTemplateProvider:
    provider_name: str
    notebook_root: Path
    pipeline_root: Path
    origin_revision: str

    @property
    def provider_revision(self) -> str:
        return self.origin_revision

    def list_notebook_templates(self) -> list[TemplateAsset]:
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

    def load_notebook_template(self, name: str) -> str:
        for asset in self.list_notebook_templates():
            if asset.name == name:
                return asset.read_text()
        raise KeyError(f'Unknown notebook template: {name}')

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

    def list_pipeline_templates(self) -> list[TemplateAsset]:
        return self.pipeline_templates()

    def load_pipeline_template(self, name: str) -> str:
        for asset in self.pipeline_templates():
            if asset.name == name:
                return asset.read_text()
        raise KeyError(f'Unknown pipeline template: {name}')


def builtin_provider() -> FilesystemTemplateProvider:
    templates_root = Path(__file__).resolve().parent
    return FilesystemTemplateProvider(
        provider_name=BUILTIN_PROVIDER,
        notebook_root=templates_root / 'builtin',
        pipeline_root=templates_root / 'pipelines',
        origin_revision='builtin@0.1.0',
    )
