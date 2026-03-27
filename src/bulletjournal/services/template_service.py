from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from bulletjournal.domain.models import TemplateRef
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.templates.provider import TemplateAsset
from bulletjournal.templates.registry import discover_template_providers
from bulletjournal.templates.validator import _pipeline_node_interface, load_pipeline_template_definition_text


@dataclass(slots=True)
class TemplateSource:
    ref: str
    provider: str
    name: str
    source_text: str
    source_hash: str
    origin_revision: str


@dataclass(slots=True)
class PipelineTemplateSource:
    ref: str
    provider: str
    name: str
    title: str
    description: str | None
    source_text: str
    source_hash: str
    definition: dict[str, Any]
    origin_revision: str


class TemplateService:
    def __init__(self) -> None:
        self._assets_by_ref = self._discover_assets()
        self._asset_aliases = self._discover_aliases(self._assets_by_ref)

    def list_templates(self) -> list[dict[str, Any]]:
        templates = [
            *self._list_notebook_templates(),
            *self._list_pipeline_templates(),
        ]
        return sorted(templates, key=lambda item: (str(item['provider']), str(item['kind']), str(item['name'])))

    def resolve_template_source(self, ref: str) -> TemplateSource:
        asset = self._require_asset(ref, kind='notebook')
        source_text = asset.read_text()
        return TemplateSource(
            ref=asset.ref,
            provider=asset.provider,
            name=asset.name,
            source_text=source_text,
            source_hash=normalized_source_hash_text(source_text),
            origin_revision=asset.origin_revision,
        )

    def resolve_pipeline_template(self, ref: str) -> PipelineTemplateSource:
        asset = self._require_asset(ref, kind='pipeline')
        source_text = asset.read_text()
        try:
            definition = load_pipeline_template_definition_text(source_text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f'Invalid pipeline template `{ref}`: {exc}.') from exc
        title = asset.title or str(definition.get('title') or Path(asset.name).stem.replace('_', ' ').title())
        description = asset.description if asset.description is not None else definition.get('description')
        return PipelineTemplateSource(
            ref=asset.ref,
            provider=asset.provider,
            name=asset.name,
            title=title,
            description=str(description) if isinstance(description, str) and description.strip() else None,
            source_text=source_text,
            source_hash=normalized_source_hash_text(source_text),
            definition=definition,
            origin_revision=asset.origin_revision,
        )

    def resolve_template_interface(self, ref: str) -> dict[str, Any]:
        asset = self._require_asset(ref, kind='notebook')
        return parse_notebook_interface(asset, node_id=Path(asset.name).stem).to_dict()

    def pipeline_node_interfaces(self, definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
        notebook_paths_by_ref = {asset.ref: asset for asset in self._assets_by_ref.values() if asset.kind == 'notebook'}
        nodes = definition.get('nodes')
        if not isinstance(nodes, list):
            raise ValueError('Pipeline template must define a `nodes` list.')
        interfaces: dict[str, dict[str, Any]] = {}
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get('id') or '').strip()
            if not node_id:
                continue
            interfaces[node_id] = _pipeline_node_interface(raw_node, notebook_paths_by_ref=notebook_paths_by_ref)
        return interfaces

    def empty_notebook_source(self, *, title: str, node_id: str) -> str:
        template = self.resolve_template_source('builtin/test_starter_notebook')
        return template.source_text.replace('{{TITLE}}', title).replace('{{NODE_ID}}', node_id)

    def template_ref(self, ref: str) -> TemplateRef:
        asset = self._require_asset(ref, kind='notebook')
        return TemplateRef(
            kind='notebook',
            provider=asset.provider,
            name=asset.name,
            ref=asset.ref,
            origin_revision=asset.origin_revision,
        )

    def _list_notebook_templates(self) -> list[dict[str, Any]]:
        templates = []
        for asset in sorted(self._assets_by_ref.values(), key=lambda item: item.ref):
            if asset.kind != 'notebook':
                continue
            if asset.ref == 'builtin/empty_notebook':
                continue
            source_text = asset.read_text()
            templates.append(
                {
                    'provider': asset.provider,
                    'kind': 'notebook',
                    'name': asset.name,
                    'ref': asset.ref,
                    'origin_revision': asset.origin_revision,
                    'hidden': asset.hidden,
                    'title': asset.title or Path(asset.name).stem.replace('_', ' ').title(),
                    'description': asset.description if asset.description is not None else asset.name,
                    'source': asset.provider,
                    'source_text': source_text,
                    'source_hash': normalized_source_hash_text(source_text),
                }
            )
        return templates

    def _list_pipeline_templates(self) -> list[dict[str, Any]]:
        templates = []
        for asset in sorted(self._assets_by_ref.values(), key=lambda item: item.ref):
            if asset.kind != 'pipeline':
                continue
            resolved = self.resolve_pipeline_template(asset.ref)
            templates.append(
                {
                    'provider': asset.provider,
                    'kind': 'pipeline',
                    'name': asset.name,
                    'ref': asset.ref,
                    'origin_revision': asset.origin_revision,
                    'hidden': False,
                    'title': resolved.title,
                    'description': resolved.description or asset.name,
                    'source': asset.provider,
                    'source_text': resolved.source_text,
                    'source_hash': resolved.source_hash,
                    'definition': resolved.definition,
                }
            )
        return templates

    def _discover_assets(self) -> dict[str, TemplateAsset]:
        assets: dict[str, TemplateAsset] = {}
        for provider in discover_template_providers():
            notebook_entries = getattr(provider, 'list_notebook_templates', lambda: [])()
            pipeline_entries = _provider_pipeline_entries(provider)
            for raw_asset in notebook_entries:
                asset = self._coerce_provider_asset(raw_asset, provider=provider, kind='notebook')
                assets[asset.ref] = asset
            for raw_asset in pipeline_entries:
                asset = self._coerce_provider_asset(raw_asset, provider=provider, kind='pipeline')
                assets[asset.ref] = asset
        return assets

    def _require_asset(self, ref: str, *, kind: str) -> TemplateAsset:
        canonical_ref = self._asset_aliases.get(ref, ref)
        asset = self._assets_by_ref.get(canonical_ref)
        if asset is None or asset.kind != kind:
            raise FileNotFoundError(f'Unknown template `{ref}`.')
        return asset

    @staticmethod
    def _discover_aliases(assets_by_ref: dict[str, TemplateAsset]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for ref, asset in assets_by_ref.items():
            aliases[ref] = ref
            aliases[f'{asset.provider}/{asset.file_name}'] = ref
            aliases[asset.file_name] = ref
            aliases[asset.name] = ref
        return aliases

    @staticmethod
    def _coerce_provider_asset(
        raw_asset: TemplateAsset | dict[str, object], *, provider: object, kind: str
    ) -> TemplateAsset:
        if isinstance(raw_asset, TemplateAsset):
            return raw_asset
        if not isinstance(raw_asset, dict):
            raise TypeError('Template providers must return TemplateAsset instances or dictionaries.')

        provider_name = str(raw_asset.get('provider') or getattr(provider, 'provider_name', '') or '').strip()
        if not provider_name:
            raise ValueError('Template provider asset is missing `provider`.')
        name = str(raw_asset.get('name') or '').strip()
        if not name:
            raise ValueError('Template provider asset is missing `name`.')
        raw_kind = str(raw_asset.get('kind') or '').strip()
        if raw_kind and raw_kind != kind:
            raise ValueError(f'Template provider asset kind mismatch: expected `{kind}`, got `{raw_kind}`.')
        ref = str(raw_asset.get('ref') or f'{provider_name}/{name}').strip()
        file_name = str(
            raw_asset.get('file_name') or raw_asset.get('path') or f'{name}.{"py" if kind == "notebook" else "json"}'
        ).strip()
        origin_revision = str(
            raw_asset.get('origin_revision') or getattr(provider, 'provider_revision', '') or ''
        ).strip()
        title = raw_asset.get('title')
        description = raw_asset.get('description')
        hidden = bool(raw_asset.get('hidden', False))
        notebook_loader = cast(Callable[[str], str] | None, getattr(provider, 'load_notebook_template', None))
        pipeline_loader = cast(Callable[[str], str] | None, getattr(provider, 'load_pipeline_template', None))

        if kind == 'notebook':
            if notebook_loader is None:
                raise ValueError('Template provider must implement `load_notebook_template(name)`.')
            source_loader = lambda name=name, notebook_loader=notebook_loader: str(notebook_loader(name))
        else:
            if pipeline_loader is None:
                raise ValueError('Template provider must implement `load_pipeline_template(name)`.')
            source_loader = lambda name=name, pipeline_loader=pipeline_loader: str(pipeline_loader(name))

        return TemplateAsset(
            provider=provider_name,
            kind=kind,
            name=name,
            file_name=file_name,
            ref=ref,
            path=None,
            origin_revision=origin_revision,
            hidden=hidden,
            title=str(title) if isinstance(title, str) and title.strip() else None,
            description=str(description) if isinstance(description, str) else None,
            source_loader=source_loader,
        )


def _provider_pipeline_entries(provider: object) -> Any:
    list_pipeline_templates = getattr(provider, 'list_pipeline_templates', None)
    if callable(list_pipeline_templates):
        return list_pipeline_templates()
    pipeline_templates = getattr(provider, 'pipeline_templates', None)
    if callable(pipeline_templates):
        return pipeline_templates()
    return []
