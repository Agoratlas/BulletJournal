from __future__ import annotations

import json
import threading
from dataclasses import replace
from typing import Any

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.errors import GraphValidationError, NotFoundError
from bulletjournal.domain.models import DashboardDocument, DashboardPanel, DashboardSource, LayoutEntry, Node
from bulletjournal.storage.atomic_write import atomic_write_text
from bulletjournal.utils import json_dumps, slugify, utc_now_iso

DASHBOARD_SCHEMA_VERSION = 1
DASHBOARD_NODE_WIDTH = 240
DASHBOARD_NODE_HEIGHT = 140
DASHBOARD_VERTICAL_OFFSET = 240
DASHBOARD_NOTEBOOK_CENTER_OFFSET = 60


class DashboardVersionConflictError(Exception):
    def __init__(self, latest_dashboard: dict[str, Any]) -> None:
        super().__init__('Dashboard version conflict.')
        self.latest_dashboard = latest_dashboard


class DashboardService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self._lock = threading.RLock()

    def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        self.project_service.get_node(dashboard_id)
        return self._read_dashboard_document(dashboard_id).to_dict()

    def create_dashboard(
        self,
        *,
        dashboard_id: str | None,
        title: str,
        sources: list[dict[str, Any]],
        panels: list[dict[str, Any]],
        x: int = 80,
        y: int = 80,
    ) -> dict[str, Any]:
        with self._lock:
            graph = self.project_service.graph()
            resolved_dashboard_id = self._next_dashboard_id(graph, dashboard_id or title)
            if any(node.id == resolved_dashboard_id for node in graph.nodes):
                raise GraphValidationError(f'Node `{resolved_dashboard_id}` already exists.')
            now = utc_now_iso()
            document = DashboardDocument(
                schema_version=DASHBOARD_SCHEMA_VERSION,
                dashboard_id=resolved_dashboard_id,
                version=1,
                title=self._validate_title(title),
                created_at=now,
                updated_at=now,
                sources=self._coerce_sources(sources, graph=graph),
                panels=self._coerce_panels(panels, graph=graph),
            )
            self._validate_panel_sources(document)
            graph.nodes.append(
                Node(
                    id=resolved_dashboard_id,
                    kind=NodeKind.DASHBOARD,
                    title=document.title,
                    ui=self._dashboard_ui(document),
                )
            )
            graph.layout.append(
                LayoutEntry(
                    node_id=resolved_dashboard_id,
                    x=int(x),
                    y=int(y),
                    w=DASHBOARD_NODE_WIDTH,
                    h=DASHBOARD_NODE_HEIGHT,
                )
            )
            self._write_dashboard_document(document)
            self.project_service.write_graph(graph)
            self._publish_dashboard_updated(document)
            return document.to_dict()

    def save_notebook_dashboard(
        self,
        node_id: str,
        *,
        dashboard_id: str | None = None,
        title: str | None,
        panels: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.NOTEBOOK:
            raise GraphValidationError(f'Node `{node_id}` does not support standalone asset dashboards.')
        graph = self.project_service.graph()
        layout = next((entry for entry in graph.layout if entry.node_id == node_id), None)
        resolved_title = self._validate_title(title or f'{node.title} dashboard')
        panel_payload = panels
        if panel_payload is None:
            assets = self.project_service.require_project().state_db.list_asset_heads(node_id=node_id)
            panel_payload = [
                {
                    'panel_id': f'{node_id}/{asset["asset_name"]}',
                    'node_id': node_id,
                    'asset_name': asset['asset_name'],
                    'visible': True,
                    'position': index,
                    'modifier_overrides': {},
                    'override_schema_hash': asset.get('override_schema_hash'),
                }
                for index, asset in enumerate(assets)
            ]
        created = self.create_dashboard(
            dashboard_id=dashboard_id,
            title=resolved_title,
            sources=[{'node_id': node_id}],
            panels=panel_payload,
            x=(layout.x + DASHBOARD_NOTEBOOK_CENTER_OFFSET) if layout is not None else 80,
            y=(layout.y - DASHBOARD_VERTICAL_OFFSET) if layout is not None else 80,
        )
        return {
            **created,
            'dashboard_url': f'/dashboards/{created["dashboard_id"]}',
        }

    def patch_dashboard(
        self,
        dashboard_id: str,
        *,
        dashboard_version: int,
        title: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        panels: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            graph = self.project_service.graph()
            existing = self._read_dashboard_document(dashboard_id)
            if existing.version != dashboard_version:
                raise DashboardVersionConflictError(existing.to_dict())
            updated = replace(
                existing,
                version=existing.version + 1,
                title=existing.title if title is None else self._validate_title(title),
                updated_at=utc_now_iso(),
                sources=existing.sources if sources is None else self._coerce_sources(sources, graph=graph),
                panels=existing.panels if panels is None else self._coerce_panels(panels, graph=graph),
            )
            self._validate_panel_sources(updated)
            self._write_dashboard_document(updated)
            graph_node = next((node for node in graph.nodes if node.id == dashboard_id), None)
            if graph_node is not None:
                graph_node.title = updated.title
                graph_node.ui = self._dashboard_ui(updated)
                self.project_service.write_graph(graph)
            self._publish_dashboard_updated(updated)
            return updated.to_dict()

    def delete_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        with self._lock:
            graph = self.project_service.graph()
            node = next((item for item in graph.nodes if item.id == dashboard_id), None)
            if node is None or node.kind != NodeKind.DASHBOARD:
                raise NotFoundError(f'Unknown dashboard `{dashboard_id}`.')
            self._read_dashboard_document(dashboard_id)
            graph.nodes = [item for item in graph.nodes if item.id != dashboard_id]
            graph.layout = [item for item in graph.layout if item.node_id != dashboard_id]
            self.project_service.write_graph(graph)
            self.delete_dashboard_file(dashboard_id)
            self.project_service.event_service.publish(
                'dashboard.deleted',
                project_id=self.project_service.require_project().metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={'dashboard_id': dashboard_id},
            )
            return {'dashboard_id': dashboard_id, 'status': 'deleted'}

    def delete_dashboard_file(self, dashboard_id: str) -> None:
        path = self.project_service.require_project().paths.dashboard_path(dashboard_id)
        if path.exists():
            path.unlink()

    def rename_dashboard(self, old_dashboard_id: str, new_dashboard_id: str, *, title: str) -> None:
        with self._lock:
            document = self._read_dashboard_document(old_dashboard_id)
            target_path = self.project_service.require_project().paths.dashboard_path(new_dashboard_id)
            if target_path.exists():
                raise GraphValidationError(f'Dashboard file `{target_path.name}` already exists.')
            updated = replace(
                document,
                dashboard_id=new_dashboard_id,
                title=self._validate_title(title),
                updated_at=utc_now_iso(),
                version=document.version + 1,
            )
            self._write_dashboard_document(updated)
            old_path = self.project_service.require_project().paths.dashboard_path(old_dashboard_id)
            if old_path.exists():
                old_path.unlink()
            self._publish_dashboard_updated(updated)

    def rename_node_references(self, old_node_id: str, new_node_id: str) -> None:
        with self._lock:
            for dashboard_id in self.list_dashboard_ids():
                document = self._read_dashboard_document(dashboard_id)
                changed = False
                next_sources: list[DashboardSource] = []
                for source in document.sources:
                    if source.node_id == old_node_id:
                        next_sources.append(DashboardSource(node_id=new_node_id))
                        changed = True
                    else:
                        next_sources.append(source)
                next_panels: list[DashboardPanel] = []
                for panel in document.panels:
                    if panel.node_id == old_node_id:
                        next_panels.append(
                            replace(
                                panel,
                                node_id=new_node_id,
                                panel_id=f'{new_node_id}/{panel.asset_name}',
                            )
                        )
                        changed = True
                    else:
                        next_panels.append(panel)
                if not changed:
                    continue
                updated = replace(
                    document,
                    version=document.version + 1,
                    updated_at=utc_now_iso(),
                    sources=next_sources,
                    panels=self._normalize_panel_positions(next_panels),
                )
                self._write_dashboard_document(updated)
                self._sync_dashboard_node_metadata(updated)
                self._publish_dashboard_updated(updated)

    def remove_node_references(self, node_id: str) -> None:
        with self._lock:
            for dashboard_id in self.list_dashboard_ids():
                if dashboard_id == node_id:
                    continue
                document = self._read_dashboard_document(dashboard_id)
                next_sources = [source for source in document.sources if source.node_id != node_id]
                next_panels = [panel for panel in document.panels if panel.node_id != node_id]
                if len(next_sources) == len(document.sources) and len(next_panels) == len(document.panels):
                    continue
                updated = replace(
                    document,
                    version=document.version + 1,
                    updated_at=utc_now_iso(),
                    sources=next_sources,
                    panels=self._normalize_panel_positions(next_panels),
                )
                self._write_dashboard_document(updated)
                self._sync_dashboard_node_metadata(updated)
                self._publish_dashboard_updated(updated)

    def list_dashboard_ids(self) -> list[str]:
        dashboards_dir = self.project_service.require_project().paths.dashboards_dir
        return sorted(path.stem for path in dashboards_dir.glob('*.json') if path.is_file())

    def materialize_template_dashboard(
        self,
        dashboard_id: str,
        *,
        title: str,
        sources: list[dict[str, Any]],
        panels: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            graph = self.project_service.graph()
            document = DashboardDocument(
                schema_version=DASHBOARD_SCHEMA_VERSION,
                dashboard_id=dashboard_id,
                version=1,
                title=self._validate_title(title),
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sources=self._coerce_sources(sources, graph=graph),
                panels=self._coerce_panels(panels, graph=graph),
            )
            self._validate_panel_sources(document)
            self._write_dashboard_document(document)
            self._sync_dashboard_node_metadata(document)
            return document.to_dict()

    def _read_dashboard_document(self, dashboard_id: str) -> DashboardDocument:
        path = self.project_service.require_project().paths.dashboard_path(dashboard_id)
        if not path.is_file():
            raise NotFoundError(f'Unknown dashboard `{dashboard_id}`.')
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise GraphValidationError(f'Dashboard `{dashboard_id}` is malformed.')
        return self._dashboard_from_dict(payload, dashboard_id=dashboard_id)

    def _write_dashboard_document(self, document: DashboardDocument) -> None:
        atomic_write_text(
            self.project_service.require_project().paths.dashboard_path(document.dashboard_id),
            json_dumps(document.to_dict(), pretty=True) + '\n',
        )

    def _dashboard_from_dict(self, payload: dict[str, Any], *, dashboard_id: str) -> DashboardDocument:
        sources = [
            DashboardSource(node_id=str(source.get('node_id') or '').strip())
            for source in payload.get('sources', [])
            if isinstance(source, dict) and str(source.get('node_id') or '').strip()
        ]
        panels = [
            DashboardPanel(
                panel_id=str(panel.get('panel_id') or '').strip()
                or f'{panel.get("node_id")}/{panel.get("asset_name")}',
                node_id=str(panel.get('node_id') or '').strip(),
                asset_name=str(panel.get('asset_name') or '').strip(),
                visible=bool(panel.get('visible', True)),
                position=int(panel.get('position', 0)),
                modifier_overrides=panel.get('modifier_overrides')
                if isinstance(panel.get('modifier_overrides'), dict)
                else {},
                override_schema_hash=None
                if panel.get('override_schema_hash') is None
                else str(panel.get('override_schema_hash')),
            )
            for panel in payload.get('panels', [])
            if isinstance(panel, dict)
        ]
        document = DashboardDocument(
            schema_version=int(payload.get('schema_version', DASHBOARD_SCHEMA_VERSION)),
            dashboard_id=str(payload.get('dashboard_id') or dashboard_id),
            version=int(payload.get('version', 1)),
            title=self._validate_title(str(payload.get('title') or dashboard_id)),
            created_at=str(payload.get('created_at') or utc_now_iso()),
            updated_at=str(payload.get('updated_at') or utc_now_iso()),
            sources=sources,
            panels=self._normalize_panel_positions(panels),
        )
        if document.dashboard_id != dashboard_id:
            raise GraphValidationError(
                f'Dashboard `{dashboard_id}` has mismatched internal id `{document.dashboard_id}`.'
            )
        return document

    def _coerce_sources(self, sources: list[dict[str, Any]], *, graph) -> list[DashboardSource]:
        if not isinstance(sources, list):
            raise GraphValidationError('Dashboard `sources` must be a list.')
        graph_node_map = {node.id: node for node in graph.nodes}
        resolved: list[DashboardSource] = []
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                raise GraphValidationError('Dashboard sources must be objects.')
            node_id = str(source.get('node_id') or '').strip()
            if not node_id:
                raise GraphValidationError('Dashboard sources must define `node_id`.')
            if node_id in seen:
                continue
            node = graph_node_map.get(node_id)
            if node is None or node.kind != NodeKind.NOTEBOOK:
                raise GraphValidationError(f'Dashboard source `{node_id}` must reference an existing notebook node.')
            resolved.append(DashboardSource(node_id=node_id))
            seen.add(node_id)
        return resolved

    def _coerce_panels(self, panels: list[dict[str, Any]], *, graph) -> list[DashboardPanel]:
        if not isinstance(panels, list):
            raise GraphValidationError('Dashboard `panels` must be a list.')
        graph_node_map = {node.id: node for node in graph.nodes}
        resolved: list[DashboardPanel] = []
        seen: set[str] = set()
        for index, panel in enumerate(panels):
            if not isinstance(panel, dict):
                raise GraphValidationError('Dashboard panels must be objects.')
            node_id = str(panel.get('node_id') or '').strip()
            asset_name = str(panel.get('asset_name') or '').strip()
            if not node_id or not asset_name:
                raise GraphValidationError('Dashboard panels must define `node_id` and `asset_name`.')
            node = graph_node_map.get(node_id)
            if node is None or node.kind != NodeKind.NOTEBOOK:
                raise GraphValidationError(
                    f'Dashboard panel `{node_id}/{asset_name}` must reference an existing notebook node.'
                )
            panel_id = str(panel.get('panel_id') or f'{node_id}/{asset_name}').strip()
            if panel_id in seen:
                raise GraphValidationError(f'Dashboard panel `{panel_id}` is duplicated.')
            seen.add(panel_id)
            resolved.append(
                DashboardPanel(
                    panel_id=panel_id,
                    node_id=node_id,
                    asset_name=asset_name,
                    visible=bool(panel.get('visible', True)),
                    position=int(panel.get('position', index)),
                    modifier_overrides=panel.get('modifier_overrides')
                    if isinstance(panel.get('modifier_overrides'), dict)
                    else {},
                    override_schema_hash=None
                    if panel.get('override_schema_hash') is None
                    else str(panel.get('override_schema_hash')),
                )
            )
        return self._normalize_panel_positions(resolved)

    def _validate_panel_sources(self, document: DashboardDocument) -> None:
        source_ids = {source.node_id for source in document.sources}
        missing = sorted({panel.node_id for panel in document.panels if panel.node_id not in source_ids})
        if missing:
            raise GraphValidationError(
                'Dashboard panels must reference notebook sources declared in `sources`: ' + ', '.join(missing)
            )

    @staticmethod
    def _normalize_panel_positions(panels: list[DashboardPanel]) -> list[DashboardPanel]:
        ordered = sorted(
            panels,
            key=lambda panel: (panel.position, panel.node_id, panel.asset_name, panel.panel_id),
        )
        return [replace(panel, position=index) for index, panel in enumerate(ordered)]

    @staticmethod
    def _validate_title(title: str) -> str:
        resolved = str(title).strip()
        if not resolved:
            raise GraphValidationError('Dashboard title is required.')
        return resolved

    def _next_dashboard_id(self, graph, base: str) -> str:
        normalized_base = slugify(base)
        existing_ids = {node.id for node in graph.nodes}
        if normalized_base not in existing_ids:
            return normalized_base
        index = 2
        while f'{normalized_base}_{index}' in existing_ids:
            index += 1
        return f'{normalized_base}_{index}'

    @staticmethod
    def _dashboard_ui(document: DashboardDocument) -> dict[str, Any]:
        return {
            'source_count': len(document.sources),
            'panel_count': len(document.panels),
        }

    def _sync_dashboard_node_metadata(self, document: DashboardDocument) -> None:
        graph = self.project_service.graph()
        graph_node = next((node for node in graph.nodes if node.id == document.dashboard_id), None)
        if graph_node is None:
            return
        if graph_node.title == document.title and graph_node.ui == self._dashboard_ui(document):
            return
        graph_node.title = document.title
        graph_node.ui = self._dashboard_ui(document)
        self.project_service.write_graph(graph)

    def _publish_dashboard_updated(self, document: DashboardDocument) -> None:
        self.project_service.event_service.publish(
            'dashboard.updated',
            project_id=self.project_service.require_project().metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={'dashboard_id': document.dashboard_id, 'version': document.version},
        )
