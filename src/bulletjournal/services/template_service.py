from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bulletjournal.domain.models import TemplateRef
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.templates.registry import TemplateAsset, discover_template_providers
from bulletjournal.templates.validator import _pipeline_node_interface, load_pipeline_template_definition


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
        source_text = asset.path.read_text(encoding='utf-8')
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
        source_text = asset.path.read_text(encoding='utf-8')
        try:
            definition = load_pipeline_template_definition(asset.path)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f'Invalid pipeline template `{ref}`: {exc}.') from exc
        title = str(definition.get('title') or Path(asset.name).stem.replace('_', ' ').title())
        description = definition.get('description')
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
        return parse_notebook_interface(asset.path, node_id=Path(asset.name).stem).to_dict()

    def pipeline_node_interfaces(self, definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
        notebook_paths_by_ref = {
            asset.ref: asset.path
            for asset in self._assets_by_ref.values()
            if asset.kind == 'notebook'
        }
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
        template = self.resolve_template_source('builtin/empty_notebook')
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
            source_text = asset.path.read_text(encoding='utf-8')
            templates.append(
                {
                    'provider': asset.provider,
                    'kind': 'notebook',
                    'name': asset.name,
                    'ref': asset.ref,
                    'origin_revision': asset.origin_revision,
                    'title': Path(asset.name).stem.replace('_', ' ').title(),
                    'description': asset.name,
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
            for asset in [*provider.notebook_templates(), *provider.pipeline_templates()]:
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
