from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from bulletjournal.domain.models import TemplateRef
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.templates.validator import _pipeline_node_interface, load_pipeline_template_definition


@dataclass(slots=True)
class TemplateSource:
    ref: str
    source_text: str
    source_hash: str


@dataclass(slots=True)
class PipelineTemplateSource:
    ref: str
    title: str
    description: str | None
    source_text: str
    source_hash: str
    definition: dict[str, Any]


class TemplateService:
    def __init__(self) -> None:
        self._builtin_dir = Path(__file__).resolve().parent.parent / 'templates' / 'builtin'
        self._pipeline_dir = Path(__file__).resolve().parent.parent / 'templates' / 'pipelines'
        self._hidden_refs = {'empty_notebook.py'}

    def list_templates(self) -> list[dict[str, Any]]:
        return [*self._list_notebook_templates(), *self._list_pipeline_templates()]

    def resolve_template_source(self, ref: str) -> TemplateSource:
        path = self._resolve_path(self._builtin_dir, ref, suffix='.py')
        source_text = path.read_text(encoding='utf-8')
        return TemplateSource(ref=ref, source_text=source_text, source_hash=normalized_source_hash_text(source_text))

    def resolve_pipeline_template(self, ref: str) -> PipelineTemplateSource:
        path = self._resolve_path(self._pipeline_dir, ref, suffix='.json')
        source_text = path.read_text(encoding='utf-8')
        try:
            definition = load_pipeline_template_definition(path)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f'Invalid pipeline template `{ref}`: {exc}.') from exc
        title = str(definition.get('title') or path.stem.replace('_', ' ').title())
        description = definition.get('description')
        return PipelineTemplateSource(
            ref=ref,
            title=title,
            description=str(description) if isinstance(description, str) and description.strip() else None,
            source_text=source_text,
            source_hash=normalized_source_hash_text(source_text),
            definition=definition,
        )

    def resolve_template_interface(self, ref: str) -> dict[str, Any]:
        path = self._resolve_path(self._builtin_dir, ref, suffix='.py')
        return parse_notebook_interface(path, node_id=path.stem).to_dict()

    def pipeline_node_interfaces(self, definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
        notebook_paths_by_ref = {
            path.relative_to(self._builtin_dir).as_posix(): path
            for path in self._builtin_dir.rglob('*.py')
            if '__pycache__' not in path.parts
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
        template = self.resolve_template_source('empty_notebook.py')
        return template.source_text.replace('{{TITLE}}', title).replace('{{NODE_ID}}', node_id)

    def template_ref(self, ref: str) -> TemplateRef:
        return TemplateRef(kind='template', ref=ref, origin_revision='builtin')

    def _list_notebook_templates(self) -> list[dict[str, Any]]:
        templates = []
        for path in sorted(self._builtin_dir.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            ref = path.relative_to(self._builtin_dir).as_posix()
            if ref in self._hidden_refs:
                continue
            source_text = path.read_text(encoding='utf-8')
            templates.append(
                {
                    'kind': 'template',
                    'ref': ref,
                    'title': path.stem.replace('_', ' ').title(),
                    'description': ref,
                    'source': 'builtin',
                    'source_text': source_text,
                    'source_hash': normalized_source_hash_text(source_text),
                }
            )
        return templates

    def _list_pipeline_templates(self) -> list[dict[str, Any]]:
        templates = []
        for path in sorted(self._pipeline_dir.rglob('*.json')):
            if '__pycache__' in path.parts:
                continue
            ref = path.relative_to(self._pipeline_dir).as_posix()
            resolved = self.resolve_pipeline_template(ref)
            templates.append(
                {
                    'kind': 'pipeline',
                    'ref': ref,
                    'title': resolved.title,
                    'description': resolved.description or ref,
                    'source': 'builtin',
                    'source_text': resolved.source_text,
                    'source_hash': resolved.source_hash,
                    'definition': resolved.definition,
                }
            )
        return templates

    @staticmethod
    def _resolve_path(root: Path, ref: str, *, suffix: str) -> Path:
        path = (root / ref).resolve()
        if path.suffix != suffix or not path.is_file() or not path.is_relative_to(root.resolve()):
            raise FileNotFoundError(f'Unknown template `{ref}`.')
        return path
