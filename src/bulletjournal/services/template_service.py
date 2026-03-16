from __future__ import annotations

from pathlib import Path
from typing import Any
from dataclasses import dataclass

from bulletjournal.domain.models import TemplateRef
from bulletjournal.parser.source_hash import normalized_source_hash_text


@dataclass(slots=True)
class TemplateSource:
    ref: str
    source_text: str
    source_hash: str


class TemplateService:
    def __init__(self) -> None:
        self._builtin_dir = Path(__file__).resolve().parent.parent / 'templates' / 'builtin'
        self._hidden_refs = {'empty_notebook.py'}

    def list_templates(self) -> list[dict[str, Any]]:
        templates = []
        for path in sorted(self._builtin_dir.glob('*.py')):
            if path.name in self._hidden_refs:
                continue
            source_text = path.read_text(encoding='utf-8')
            templates.append(
                {
                    'kind': 'template',
                    'ref': path.name,
                    'title': path.stem.replace('_', ' ').title(),
                    'source': 'builtin',
                    'source_text': source_text,
                    'source_hash': normalized_source_hash_text(source_text),
                }
            )
        return templates

    def resolve_template_source(self, ref: str) -> TemplateSource:
        path = self._builtin_dir / ref
        if not path.exists():
            raise FileNotFoundError(f'Unknown template `{ref}`.')
        source_text = path.read_text(encoding='utf-8')
        return TemplateSource(ref=ref, source_text=source_text, source_hash=normalized_source_hash_text(source_text))

    def empty_notebook_source(self, *, title: str, node_id: str) -> str:
        template = self.resolve_template_source('empty_notebook.py')
        return template.source_text.replace('{{TITLE}}', title).replace('{{NODE_ID}}', node_id)

    def template_ref(self, ref: str) -> TemplateRef:
        return TemplateRef(kind='template', ref=ref, origin_revision='builtin')
