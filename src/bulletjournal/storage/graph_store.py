from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import Edge, GraphData, LayoutEntry, Node, TemplateRef
from bulletjournal.storage.atomic_write import atomic_write_text
from bulletjournal.storage.project_fs import ProjectPaths
from bulletjournal.utils import json_dumps, utc_now_iso


class GraphStore:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._lock = threading.RLock()

    def read(self) -> GraphData:
        with self._lock:
            meta = self._read_dict(self.paths.graph_dir / 'meta.json')
            nodes = [self._node_from_dict(item) for item in self._read_list(self.paths.graph_dir / 'nodes.json')]
            edges = [Edge(**item) for item in self._read_list(self.paths.graph_dir / 'edges.json')]
            layout = [LayoutEntry(**item) for item in self._read_list(self.paths.graph_dir / 'layout.json')]
            return GraphData(meta=meta, nodes=nodes, edges=edges, layout=layout)

    def write(self, graph: GraphData, *, increment_version: bool = True) -> GraphData:
        with self._lock:
            meta = dict(graph.meta)
            if increment_version:
                meta['graph_version'] = int(meta.get('graph_version', 0)) + 1
            meta['updated_at'] = utc_now_iso()
            nodes = sorted(graph.nodes, key=lambda item: item.id)
            edges = sorted(graph.edges, key=lambda item: item.id)
            layout = sorted(graph.layout, key=lambda item: item.node_id)
            self._atomic_replace_graph_dir(
                meta=meta,
                nodes=[node.to_dict() for node in nodes],
                edges=[edge.to_dict() for edge in edges],
                layout=[entry.to_dict() for entry in layout],
            )
            return GraphData(meta=meta, nodes=nodes, edges=edges, layout=layout)

    def _atomic_replace_graph_dir(
        self,
        *,
        meta: dict[str, object],
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
        layout: list[dict[str, object]],
    ) -> None:
        parent = self.paths.graph_dir.parent
        temp_dir = parent / f'.graph.{uuid.uuid4().hex}.tmp'
        backup_dir = parent / f'.graph.{uuid.uuid4().hex}.bak'
        temp_dir.mkdir(parents=True, exist_ok=False)
        try:
            self._write_graph_files(temp_dir, meta=meta, nodes=nodes, edges=edges, layout=layout)
            replaced_existing = False
            if self.paths.graph_dir.exists():
                os.replace(self.paths.graph_dir, backup_dir)
                replaced_existing = True
            try:
                os.replace(temp_dir, self.paths.graph_dir)
            except Exception:
                if replaced_existing and backup_dir.exists() and not self.paths.graph_dir.exists():
                    os.replace(backup_dir, self.paths.graph_dir)
                raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

    @staticmethod
    def _write_graph_files(
        directory: Path,
        *,
        meta: dict[str, object],
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
        layout: list[dict[str, object]],
    ) -> None:
        atomic_write_text(directory / 'meta.json', json_dumps(meta, pretty=True) + '\n')
        atomic_write_text(directory / 'nodes.json', json_dumps(nodes, pretty=True) + '\n')
        atomic_write_text(directory / 'edges.json', json_dumps(edges, pretty=True) + '\n')
        atomic_write_text(directory / 'layout.json', json_dumps(layout, pretty=True) + '\n')

    @staticmethod
    def _read_dict(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f'Expected JSON object in {path}')
        return {str(key): value for key, value in data.items()}

    @staticmethod
    def _read_list(path: Path) -> list[dict[str, Any]]:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError(f'Expected JSON array in {path}')
        rows: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(f'Expected object entries in {path}')
            rows.append({str(key): value for key, value in item.items()})
        return rows

    @staticmethod
    def _node_from_dict(data: dict[str, Any]) -> Node:
        template_data = data.get('template')
        template = None
        if isinstance(template_data, dict):
            ref = str(template_data.get('ref') or '')
            provider = str(template_data.get('provider') or 'builtin')
            name = str(template_data.get('name') or ref)
            kind = str(template_data.get('kind') or 'notebook')
            template = TemplateRef(
                kind=kind,
                provider=provider,
                name=name,
                ref=ref,
                origin_revision=None if template_data.get('origin_revision') is None else str(template_data.get('origin_revision')),
            )
        path_value = data.get('path')
        resolved_path = str(path_value) if isinstance(path_value, str) else None
        ui_value = data.get('ui')
        resolved_ui = ui_value if isinstance(ui_value, dict) else {'hidden_inputs': []}
        return Node(
            id=str(data['id']),
            kind=NodeKind(str(data['kind'])),
            title=str(data['title']),
            path=resolved_path,
            template=template,
            ui=resolved_ui,
        )
