from __future__ import annotations

import re
from typing import Any

from bulletjournal.domain.graph_bindings import (
    organizer_interface_for_node,
    organizer_ports_from_ui,
    resolve_input_binding,
)
from bulletjournal.domain.hashing import combine_hashes, hash_json
from bulletjournal.domain.errors import NotFoundError
from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.domain.graph_rules import (
    assert_node_exists,
    validate_acyclic,
    validate_unique_edge_ids,
    validate_unique_node_ids,
    validate_unique_target_ports,
)
from bulletjournal.domain.models import Edge, GraphData, LayoutEntry, Node, file_input_artifact_name
from bulletjournal.domain.type_system import types_compatible
from bulletjournal.execution.planner import downstream_closure, topological_nodes, visible_edge_id


class GraphService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def get_graph(self) -> dict[str, Any]:
        graph = self.project_service.graph()
        return {
            'meta': graph.meta,
            'nodes': [node.to_dict() for node in graph.nodes],
            'edges': [edge.to_dict() for edge in graph.edges],
            'layout': [entry.to_dict() for entry in graph.layout],
        }

    def apply_operations(self, graph_version: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        graph = self.project_service.graph()
        if int(graph.meta['graph_version']) != graph_version:
            raise GraphValidationError('Graph version conflict.')
        self._assert_operations_allowed_with_frozen_blocks(graph, operations)
        active_run_interruption = None
        reparse_all = False
        stale_roots: set[str] = set()
        interruption_roots: set[str] = set()
        pending_notebook_creates: list[tuple[str, str]] = []
        pending_notebook_deletes: list[str] = []
        pending_editor_stops: list[str] = []
        pending_file_input_heads: list[str] = []
        pending_state_deletes: list[str] = []
        for operation in operations:
            op_type = operation['type']
            if op_type == 'add_notebook_node':
                node_id, source = self._add_notebook_node(graph, operation)
                pending_notebook_creates.append((node_id, source))
                reparse_all = True
                interruption_roots.add(node_id)
            elif op_type == 'add_file_input_node':
                node_id = self._add_file_input_node(graph, operation)
                pending_file_input_heads.append(node_id)
                interruption_roots.add(node_id)
            elif op_type == 'add_organizer_node':
                self._add_organizer_node(graph, operation)
            elif op_type == 'add_area_node':
                self._add_area_node(graph, operation)
            elif op_type == 'add_pipeline_template':
                created = self._add_pipeline_template(graph, operation)
                pending_notebook_creates.extend(created['notebook_creates'])
                pending_file_input_heads.extend(created['file_input_heads'])
                reparse_all = True
                interruption_roots.update(node_id for node_id, _ in created['notebook_creates'])
                interruption_roots.update(created['file_input_heads'])
            elif op_type == 'add_edge':
                self._add_edge(graph, operation)
                stale_roots.add(str(operation['target_node']))
                interruption_roots.add(str(operation['target_node']))
            elif op_type == 'remove_edge':
                edge = self._remove_edge(graph, str(operation['edge_id']))
                if edge is not None:
                    stale_roots.add(edge.target_node)
                    interruption_roots.add(edge.target_node)
            elif op_type == 'update_node_layout':
                self._update_layout(graph, operation)
            elif op_type == 'update_node_title':
                self._update_title(graph, operation)
            elif op_type == 'update_organizer_ports':
                removed_targets = self._update_organizer_ports(graph, operation)
                stale_roots.update(removed_targets)
                interruption_roots.update(removed_targets)
            elif op_type == 'update_area_style':
                self._update_area_style(graph, operation)
            elif op_type == 'update_node_frozen':
                pending_editor_stops.extend(self._update_frozen(graph, operation))
            elif op_type == 'delete_node':
                deleted = self._delete_node(graph, str(operation['node_id']))
                stale_roots.update(deleted['stale_roots'])
                interruption_roots.update(deleted['stale_roots'])
                interruption_roots.add(str(deleted['node_id']))
                if deleted['delete_notebook_file']:
                    pending_notebook_deletes.append(str(deleted['node_id']))
                    pending_editor_stops.append(str(deleted['node_id']))
                pending_state_deletes.append(str(deleted['node_id']))
                reparse_all = True
            else:
                raise GraphValidationError(f'Unsupported graph operation `{op_type}`.')
        self._validate_graph(graph)
        if interruption_roots and self.project_service.run_service is not None:
            active_run_interruption = self.project_service.run_service.interrupt_active_run_if_nodes_affected(
                sorted(interruption_roots),
                graph,
            )
        graph = self.project_service.write_graph(graph)
        for node_id, source in pending_notebook_creates:
            self.project_service.require_project().paths.notebook_path(node_id).write_text(source, encoding='utf-8')
        for node_id in pending_editor_stops:
            if self.project_service.run_service is not None:
                self.project_service.run_service.session_manager.stop_by_node(node_id)
        for node_id in pending_file_input_heads:
            node = next(node for node in graph.nodes if node.id == node_id)
            self.project_service.require_project().state_db.ensure_artifact_head(
                node_id,
                file_input_artifact_name(node),
                ArtifactState.PENDING,
            )
        for node_id in pending_notebook_deletes:
            path = self.project_service.require_project().paths.notebook_path(node_id)
            if path.exists():
                path.unlink()
        for node_id in pending_state_deletes:
            self.project_service.require_project().state_db.delete_node_state(node_id)
        if reparse_all:
            self.project_service.reparse_all_notebooks()
        if stale_roots:
            self.mark_nodes_and_downstream_stale(sorted(stale_roots))
            self.restore_nodes_and_downstream_ready_if_lineage_matches(sorted(stale_roots))
        snapshot = self.project_service.snapshot()
        graph_payload = snapshot['graph']
        return {
            'meta': graph_payload['meta'],
            'nodes': graph_payload['nodes'],
            'edges': graph_payload['edges'],
            'layout': graph_payload['layout'],
            'interrupted_run': active_run_interruption,
        }

    def remove_edges_for_port_changes(
        self,
        *,
        node_id: str,
        removed_source_ports: list[str],
        removed_target_ports: list[str],
    ) -> list[dict[str, Any]]:
        graph = self.project_service.graph()
        kept_edges: list[Edge] = []
        removed: list[dict[str, Any]] = []
        stale_roots: set[str] = set()
        for edge in graph.edges:
            remove = False
            if edge.source_node == node_id and edge.source_port in removed_source_ports:
                remove = True
                stale_roots.add(edge.target_node)
            if edge.target_node == node_id and edge.target_port in removed_target_ports:
                remove = True
                stale_roots.add(node_id)
            if remove:
                removed.append(edge.to_dict())
            else:
                kept_edges.append(edge)
        if removed:
            graph.edges = kept_edges
            self.project_service.write_graph(graph)
            if stale_roots:
                self.mark_nodes_and_downstream_stale(sorted(stale_roots))
        return removed

    def mark_downstream_stale(self, node_ids: list[str]) -> None:
        graph = self.project_service.graph()
        affected: set[str] = set()
        for node_id in node_ids:
            affected.update(downstream_closure(graph, node_id))
        self._mark_nodes_stale(sorted(affected), graph)

    def mark_nodes_and_downstream_stale(self, node_ids: list[str]) -> None:
        graph = self.project_service.graph()
        affected: set[str] = set(node_ids)
        for node_id in node_ids:
            affected.update(downstream_closure(graph, node_id))
        self._mark_nodes_stale(sorted(affected), graph)

    def restore_nodes_and_downstream_ready_if_lineage_matches(self, node_ids: list[str]) -> None:
        graph = self.project_service.graph()
        if not node_ids:
            return
        affected: set[str] = set(node_ids)
        for node_id in node_ids:
            affected.update(downstream_closure(graph, node_id))
        ordered_ids = [node_id for node_id in topological_nodes(graph) if node_id in affected]
        for node_id in ordered_ids:
            self._restore_ready_outputs_for_node(node_id, graph)

    def _mark_nodes_stale(self, node_ids: list[str], graph: GraphData) -> None:
        project = self.project_service.require_project()
        if not node_ids:
            return
        for downstream_node in node_ids:
            try:
                interface = self.project_service.latest_interface(downstream_node)
            except NotFoundError:
                continue
            if interface is None:
                continue
            for port in interface.get('outputs', []) + interface.get('assets', []):
                head = project.state_db.get_artifact_head(downstream_node, port['name'])
                if head and head['current_version_id'] is not None:
                    old_state = head['state']
                    if old_state != ArtifactState.STALE.value:
                        project.state_db.set_artifact_head_state(downstream_node, port['name'], ArtifactState.STALE)
                        self.project_service.event_service.publish(
                            'artifact.state_changed',
                            project_id=project.metadata.project_id,
                            graph_version=int(graph.meta['graph_version']),
                            payload={
                                'node_id': downstream_node,
                                'artifact_name': port['name'],
                                'old_state': old_state,
                                'new_state': ArtifactState.STALE.value,
                            },
                        )

    def _restore_ready_outputs_for_node(self, node_id: str, graph: GraphData) -> None:
        project = self.project_service.require_project()
        node = next((item for item in graph.nodes if item.id == node_id), None)
        if node is None or node.kind != NodeKind.NOTEBOOK:
            return
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return
        source_hash = interface.get('source_hash')
        if not isinstance(source_hash, str) or not source_hash:
            return

        input_hashes: list[str] = []
        input_code_hashes: list[str] = []
        for port in interface.get('inputs', []):
            metadata = self._lineage_metadata_for_input(node_id, port, graph)
            if metadata is None:
                return
            input_hashes.append(metadata['artifact_hash'])
            input_code_hashes.append(metadata['upstream_code_hash'])

        for port in interface.get('outputs', []) + interface.get('assets', []):
            artifact_name = str(port['name'])
            head = project.state_db.get_artifact_head(node_id, artifact_name)
            if head is None or head.get('current_version_id') is None:
                continue
            if head['state'] != ArtifactState.STALE.value:
                continue
            expected_upstream_data_hash = combine_hashes([source_hash, f'{node_id}/{artifact_name}', *input_hashes])
            expected_upstream_code_hash = combine_hashes(
                [source_hash, f'{node_id}/{artifact_name}', *input_code_hashes]
            )
            if (
                head.get('source_hash') != source_hash
                or head.get('upstream_data_hash') != expected_upstream_data_hash
                or head.get('upstream_code_hash') != expected_upstream_code_hash
            ):
                continue
            project.state_db.set_artifact_head_state(
                node_id,
                artifact_name,
                ArtifactState.READY,
            )
            self.project_service.event_service.publish(
                'artifact.state_changed',
                project_id=project.metadata.project_id,
                graph_version=int(graph.meta['graph_version']),
                payload={
                    'node_id': node_id,
                    'artifact_name': artifact_name,
                    'old_state': ArtifactState.STALE.value,
                    'new_state': ArtifactState.READY.value,
                },
            )

    def _lineage_metadata_for_input(
        self,
        node_id: str,
        port: dict[str, Any],
        graph: GraphData,
    ) -> dict[str, str] | None:
        binding = resolve_input_binding(graph, node_id=node_id, input_name=str(port['name']))
        if binding is None:
            if bool(port.get('has_default', False)):
                return {
                    'artifact_hash': hash_json(port.get('default')),
                    'upstream_code_hash': 'default',
                }
            return None
        head = self.project_service.require_project().state_db.get_artifact_head(
            binding[0],
            binding[1],
        )
        if head is None or head.get('current_version_id') is None:
            return None
        if head.get('state') != ArtifactState.READY.value:
            return None
        artifact_hash = head.get('artifact_hash')
        upstream_code_hash = head.get('upstream_code_hash')
        if not isinstance(artifact_hash, str) or not artifact_hash:
            return None
        if not isinstance(upstream_code_hash, str) or not upstream_code_hash:
            return None
        return {
            'artifact_hash': artifact_hash,
            'upstream_code_hash': upstream_code_hash,
        }

    def _add_notebook_node(self, graph: GraphData, operation: dict[str, Any]) -> tuple[str, str]:
        node_id = str(operation['node_id'])
        title = str(operation['title'])
        if any(node.id == node_id for node in graph.nodes):
            raise GraphValidationError(f'Node `{node_id}` already exists.')
        template_ref = operation.get('template_ref')
        source_text = operation.get('source_text')
        ui = operation.get('ui')
        template = (
            self.project_service.template_service.resolve_template_source(str(template_ref), allow_inactive=False)
            if template_ref
            else None
        )
        node = Node(
            id=node_id,
            kind=NodeKind.NOTEBOOK,
            title=title,
            path=self.project_service.require_project().paths.notebook_relpath(node_id),
            template=None
            if template_ref is None or source_text is not None
            else self.project_service.template_service.template_ref(str(template_ref)),
            ui={**({'hidden_inputs': []}), **ui} if isinstance(ui, dict) else {'hidden_inputs': []},
        )
        graph.nodes.append(node)
        graph.layout.append(self._layout_entry(node_id, operation))
        source = (
            str(source_text)
            if source_text is not None
            else template.source_text
            if template is not None
            else self.project_service.template_service.empty_notebook_source(title=title, node_id=node_id)
        )
        return node_id, source

    def _add_file_input_node(self, graph: GraphData, operation: dict[str, Any]) -> str:
        node_id = str(operation['node_id'])
        title = str(operation['title'])
        if any(node.id == node_id for node in graph.nodes):
            raise GraphValidationError(f'Node `{node_id}` already exists.')
        artifact_name = str(operation.get('artifact_name', 'file'))
        ui = operation.get('ui')
        graph.nodes.append(
            Node(
                id=node_id,
                kind=NodeKind.FILE_INPUT,
                title=title,
                ui={**({'hidden_inputs': [], 'artifact_name': artifact_name}), **ui}
                if isinstance(ui, dict)
                else {'hidden_inputs': [], 'artifact_name': artifact_name},
            )
        )
        graph.layout.append(self._layout_entry(node_id, operation))
        return node_id

    def _add_organizer_node(self, graph: GraphData, operation: dict[str, Any]) -> str:
        node_id = str(operation['node_id'])
        title = str(operation.get('title') or 'Organizer')
        if any(node.id == node_id for node in graph.nodes):
            raise GraphValidationError(f'Node `{node_id}` already exists.')
        ui = operation.get('ui')
        resolved_ui = (
            {**({'hidden_inputs': [], 'organizer_ports': []}), **ui}
            if isinstance(ui, dict)
            else {
                'hidden_inputs': [],
                'organizer_ports': [],
            }
        )
        resolved_ui['organizer_ports'] = _coerce_organizer_ports(resolved_ui.get('organizer_ports', []))
        graph.nodes.append(
            Node(
                id=node_id,
                kind=NodeKind.ORGANIZER,
                title=title,
                ui=resolved_ui,
            )
        )
        graph.layout.append(self._layout_entry(node_id, operation))
        return node_id

    def _add_area_node(self, graph: GraphData, operation: dict[str, Any]) -> str:
        node_id = str(operation['node_id'])
        title = 'Area' if operation.get('title') is None else str(operation.get('title'))
        if any(node.id == node_id for node in graph.nodes):
            raise GraphValidationError(f'Node `{node_id}` already exists.')
        ui = operation.get('ui')
        resolved_ui = _coerce_area_ui(ui)
        graph.nodes.append(
            Node(
                id=node_id,
                kind=NodeKind.AREA,
                title=title,
                ui=resolved_ui,
            )
        )
        graph.layout.append(self._layout_entry(node_id, operation))
        return node_id

    def _add_pipeline_template(self, graph: GraphData, operation: dict[str, Any]) -> dict[str, list[Any]]:
        template_ref = str(operation['template_ref'])
        pipeline = self.project_service.template_service.resolve_pipeline_template(template_ref, allow_inactive=False)
        definition = pipeline.definition
        nodes = definition.get('nodes')
        edges = definition.get('edges')
        layout_rows = definition.get('layout')
        if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(layout_rows, list):
            raise GraphValidationError(f'Pipeline template `{template_ref}` is malformed.')

        layout_by_node = {
            str(item['node_id']): item
            for item in layout_rows
            if isinstance(item, dict) and item.get('node_id') is not None
        }
        min_x = min((int(item.get('x', 0)) for item in layout_rows if isinstance(item, dict)), default=0)
        min_y = min((int(item.get('y', 0)) for item in layout_rows if isinstance(item, dict)), default=0)
        offset_x = int(operation.get('x', 80)) - min_x
        offset_y = int(operation.get('y', 80)) - min_y
        node_id_prefix = _normalize_node_id_prefix(operation.get('node_id_prefix'))
        title_prefix = _normalize_title_prefix(operation.get('node_id_prefix'))

        notebook_creates: list[tuple[str, str]] = []
        file_input_heads: list[str] = []
        node_id_map: dict[str, str] = {}
        interfaces_by_node: dict[str, dict[str, Any]] = self.project_service.template_service.pipeline_node_interfaces(
            definition
        )

        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                raise GraphValidationError(f'Pipeline template `{template_ref}` contains an invalid node entry.')
            template_node_id = str(raw_node.get('id') or '').strip()
            if not template_node_id:
                raise GraphValidationError(f'Pipeline template `{template_ref}` contains a node without an id.')
            layout = layout_by_node.get(template_node_id)
            if layout is None:
                raise GraphValidationError(
                    f'Pipeline template `{template_ref}` is missing layout for `{template_node_id}`.'
                )
            resolved_node_id = f'{node_id_prefix}{template_node_id}' if node_id_prefix else template_node_id
            if any(node.id == resolved_node_id for node in graph.nodes) or resolved_node_id in node_id_map.values():
                raise GraphValidationError(
                    f'Pipeline template `{template_ref}` would create duplicate node `{resolved_node_id}`. Use a prefix to instantiate it.'
                )
            resolved_title = _apply_title_prefix(str(raw_node.get('title') or template_node_id), title_prefix)
            kind = str(raw_node.get('kind') or '')
            if kind == NodeKind.NOTEBOOK.value:
                add_operation = {
                    'node_id': resolved_node_id,
                    'title': resolved_title,
                    'template_ref': raw_node.get('template_ref'),
                    'ui': raw_node.get('ui'),
                    'x': int(layout.get('x', 80)) + offset_x,
                    'y': int(layout.get('y', 80)) + offset_y,
                    'w': int(layout.get('w', 320)),
                    'h': int(layout.get('h', 220)),
                }
                node_id, source = self._add_notebook_node(graph, add_operation)
                notebook_creates.append((node_id, source))
            elif kind == NodeKind.FILE_INPUT.value:
                add_operation = {
                    'node_id': resolved_node_id,
                    'title': resolved_title,
                    'artifact_name': raw_node.get('artifact_name') or raw_node.get('ui', {}).get('artifact_name')
                    if isinstance(raw_node.get('ui'), dict)
                    else 'file',
                    'ui': raw_node.get('ui') if isinstance(raw_node.get('ui'), dict) else None,
                    'x': int(layout.get('x', 80)) + offset_x,
                    'y': int(layout.get('y', 80)) + offset_y,
                    'w': int(layout.get('w', 320)),
                    'h': int(layout.get('h', 220)),
                }
                node_id = self._add_file_input_node(graph, add_operation)
                file_input_heads.append(node_id)
            elif kind == NodeKind.ORGANIZER.value:
                add_operation = {
                    'node_id': resolved_node_id,
                    'title': resolved_title,
                    'ui': raw_node.get('ui') if isinstance(raw_node.get('ui'), dict) else None,
                    'x': int(layout.get('x', 80)) + offset_x,
                    'y': int(layout.get('y', 80)) + offset_y,
                    'w': int(layout.get('w', 160)),
                    'h': int(layout.get('h', 120)),
                }
                self._add_organizer_node(graph, add_operation)
            elif kind == NodeKind.AREA.value:
                add_operation = {
                    'node_id': resolved_node_id,
                    'title': resolved_title,
                    'ui': raw_node.get('ui') if isinstance(raw_node.get('ui'), dict) else None,
                    'x': int(layout.get('x', 80)) + offset_x,
                    'y': int(layout.get('y', 80)) + offset_y,
                    'w': int(layout.get('w', 480)),
                    'h': int(layout.get('h', 280)),
                }
                self._add_area_node(graph, add_operation)
            else:
                raise GraphValidationError(
                    f'Pipeline template `{template_ref}` contains unsupported node kind `{kind}`.'
                )
            node_id_map[template_node_id] = resolved_node_id

        for raw_edge in edges:
            if not isinstance(raw_edge, dict):
                raise GraphValidationError(f'Pipeline template `{template_ref}` contains an invalid edge entry.')
            source_node = node_id_map.get(str(raw_edge.get('source_node') or ''))
            target_node = node_id_map.get(str(raw_edge.get('target_node') or ''))
            if source_node is None or target_node is None:
                raise GraphValidationError(f'Pipeline template `{template_ref}` references an unknown edge endpoint.')
            source_interface = interfaces_by_node.get(str(raw_edge.get('source_node') or ''))
            target_interface = interfaces_by_node.get(str(raw_edge.get('target_node') or ''))
            if source_interface is None or target_interface is None:
                raise GraphValidationError(f'Pipeline template `{template_ref}` could not resolve node interfaces.')
            self._add_edge_from_interfaces(
                graph,
                {
                    'source_node': source_node,
                    'source_port': str(raw_edge.get('source_port') or ''),
                    'target_node': target_node,
                    'target_port': str(raw_edge.get('target_port') or ''),
                },
                source_interface=source_interface,
                target_interface=target_interface,
            )

        return {'notebook_creates': notebook_creates, 'file_input_heads': file_input_heads}

    def _add_edge(self, graph: GraphData, operation: dict[str, Any]) -> None:
        source_node = str(operation['source_node'])
        source_port = str(operation['source_port'])
        target_node = str(operation['target_node'])
        target_port = str(operation['target_port'])
        node_ids = {node.id for node in graph.nodes}
        assert_node_exists(node_ids, source_node)
        assert_node_exists(node_ids, target_node)
        source_interface = self._interface_for_graph_node(graph, source_node)
        target_interface = self._interface_for_graph_node(graph, target_node)
        if source_interface is None or target_interface is None:
            raise GraphValidationError('Cannot connect nodes without parsed interfaces.')
        self._add_edge_from_interfaces(
            graph,
            operation,
            source_interface=source_interface,
            target_interface=target_interface,
        )

    def _interface_for_graph_node(self, graph: GraphData, node_id: str) -> dict[str, Any] | None:
        node = next((item for item in graph.nodes if item.id == node_id), None)
        if node is None:
            return None
        if node.kind == NodeKind.FILE_INPUT:
            return self.project_service.synthetic_file_input_interface(node).to_dict()
        if node.kind == NodeKind.ORGANIZER:
            return organizer_interface_for_node(node).to_dict()
        if node.kind == NodeKind.AREA:
            return {'inputs': [], 'outputs': [], 'assets': []}
        return self.project_service.latest_interface(node_id)

    def _add_edge_from_interfaces(
        self,
        graph: GraphData,
        operation: dict[str, Any],
        *,
        source_interface: dict[str, Any],
        target_interface: dict[str, Any],
    ) -> None:
        source_node = str(operation['source_node'])
        source_port = str(operation['source_port'])
        target_node = str(operation['target_node'])
        target_port = str(operation['target_port'])
        source_type = _port_data_type(
            source_interface.get('outputs', []) + source_interface.get('assets', []), source_port
        )
        target_type = _port_data_type(target_interface.get('inputs', []), target_port)
        if source_type is None:
            raise GraphValidationError(f'Unknown source port `{source_port}`.')
        if target_type is None:
            raise GraphValidationError(f'Unknown target port `{target_port}`.')
        if not types_compatible(source_type, target_type):
            raise GraphValidationError(f'Cannot connect `{source_type}` to `{target_type}`.')
        edge = Edge(
            id=visible_edge_id(
                Edge(
                    id='',
                    source_node=source_node,
                    source_port=source_port,
                    target_node=target_node,
                    target_port=target_port,
                )
            ),
            source_node=source_node,
            source_port=source_port,
            target_node=target_node,
            target_port=target_port,
        )
        graph.edges = [existing for existing in graph.edges if existing.id != edge.id]
        graph.edges.append(edge)

    def _remove_edge(self, graph: GraphData, edge_id: str) -> Edge | None:
        existing = [edge for edge in graph.edges if edge.id == edge_id]
        if not existing:
            return None
        graph.edges = [edge for edge in graph.edges if edge.id != edge_id]
        return existing[0]

    def _update_layout(self, graph: GraphData, operation: dict[str, Any]) -> None:
        node_id = str(operation['node_id'])
        for index, entry in enumerate(graph.layout):
            if entry.node_id == node_id:
                width = entry.w if operation.get('w') is None else int(operation['w'])
                height = entry.h if operation.get('h') is None else int(operation['h'])
                graph.layout[index] = LayoutEntry(
                    node_id=node_id,
                    x=int(operation.get('x', entry.x)),
                    y=int(operation.get('y', entry.y)),
                    w=width,
                    h=height,
                )
                return
        graph.layout.append(self._layout_entry(node_id, operation))

    def _update_title(self, graph: GraphData, operation: dict[str, Any]) -> None:
        node_id = str(operation['node_id'])
        title = str(operation['title'])
        for node in graph.nodes:
            if node.id == node_id:
                node.title = title
                return
        raise GraphValidationError(f'Unknown node `{node_id}`.')

    def _update_organizer_ports(self, graph: GraphData, operation: dict[str, Any]) -> list[str]:
        node_id = str(operation['node_id'])
        ports = _coerce_organizer_ports(operation.get('ports', []))
        node = next((item for item in graph.nodes if item.id == node_id), None)
        if node is None or node.kind != NodeKind.ORGANIZER:
            raise GraphValidationError(f'Unknown organizer node `{node_id}`.')
        previous_ports = organizer_ports_from_ui(node.ui)
        next_by_key = {port['key']: port for port in ports}
        removed_keys = [
            previous['key']
            for previous in previous_ports
            if previous['key'] not in next_by_key or next_by_key[previous['key']]['data_type'] != previous['data_type']
        ]
        stale_roots = sorted(
            {
                edge.target_node
                for edge in graph.edges
                if edge.source_node == node_id and edge.source_port in removed_keys
            }
        )
        if removed_keys:
            graph.edges = [
                edge
                for edge in graph.edges
                if not (
                    (edge.source_node == node_id and edge.source_port in removed_keys)
                    or (edge.target_node == node_id and edge.target_port in removed_keys)
                )
            ]
        node.ui = {**node.ui, 'organizer_ports': ports}
        return stale_roots

    def _update_area_style(self, graph: GraphData, operation: dict[str, Any]) -> None:
        node_id = str(operation['node_id'])
        node = next((item for item in graph.nodes if item.id == node_id), None)
        if node is None or node.kind != NodeKind.AREA:
            raise GraphValidationError(f'Unknown area node `{node_id}`.')
        node.ui = {
            **node.ui,
            **_coerce_area_ui(
                {
                    'title_position': operation.get('title_position'),
                    'area_color': operation.get('color'),
                    'area_filled': operation.get('filled'),
                }
            ),
        }

    def _update_frozen(self, graph: GraphData, operation: dict[str, Any]) -> list[str]:
        node_id = str(operation['node_id'])
        frozen = bool(operation.get('frozen', False))
        target_node = next((node for node in graph.nodes if node.id == node_id), None)
        if target_node is None:
            raise GraphValidationError(f'Unknown node `{node_id}`.')
        if not frozen:
            unfrozen_ids = set(downstream_closure(graph, node_id)) | {node_id}
            for node in graph.nodes:
                if node.id not in unfrozen_ids:
                    continue
                node.ui = {**node.ui, 'frozen': False}
            return []

        blockers = self.project_service.active_editor_upstream_blockers_for_freeze(node_id, graph=graph)
        if blockers:
            raise GraphValidationError(self.project_service.freeze_upstream_editor_block_message(blockers))

        frozen_ids: list[str] = []
        for node in self.project_service.freeze_targets_for_node(node_id, graph=graph):
            if not node.ui.get('frozen'):
                frozen_ids.append(node.id)
            node.ui = {**node.ui, 'frozen': True}
        return frozen_ids

    def _assert_operations_allowed_with_frozen_blocks(
        self,
        graph: GraphData,
        operations: list[dict[str, Any]],
    ) -> None:
        for operation in operations:
            blockers = self._frozen_block_blockers_for_operation(graph, operation)
            if blockers:
                raise GraphValidationError(self.project_service.freeze_block_message(blockers))

    def _frozen_block_blockers_for_operation(
        self,
        graph: GraphData,
        operation: dict[str, Any],
    ) -> list[Node]:
        op_type = operation['type']
        if op_type == 'add_edge':
            return self.project_service.frozen_block_blockers_for_stale_roots(
                [str(operation['target_node'])], graph=graph
            )
        if op_type == 'remove_edge':
            edge_id = str(operation['edge_id'])
            edge = next((item for item in graph.edges if item.id == edge_id), None)
            if edge is None:
                return []
            return self.project_service.frozen_block_blockers_for_stale_roots([edge.target_node], graph=graph)
        if op_type == 'update_organizer_ports':
            node_id = str(operation['node_id'])
            return self.project_service.frozen_block_blockers_for_node_edit(node_id, graph=graph)
        if op_type == 'update_area_style':
            node_id = str(operation['node_id'])
            node = next((item for item in graph.nodes if item.id == node_id), None)
            if node is not None and node.kind == NodeKind.AREA:
                return []
            return self.project_service.frozen_block_blockers_for_node_edit(node_id, graph=graph)
        if op_type != 'delete_node':
            return []
        node_id = str(operation['node_id'])
        node = next((item for item in graph.nodes if item.id == node_id), None)
        blockers = []
        if node is not None and node.kind != NodeKind.AREA and self.project_service.block_is_frozen(node):
            blockers.append(node)
        stale_roots = sorted({edge.target_node for edge in graph.edges if edge.source_node == node_id})
        downstream_blockers = self.project_service.frozen_block_blockers_for_stale_roots(
            stale_roots,
            graph=graph,
        )
        seen = {item.id for item in blockers}
        for blocker in downstream_blockers:
            if blocker.id not in seen:
                blockers.append(blocker)
                seen.add(blocker.id)
        return blockers

    def _delete_node(self, graph: GraphData, node_id: str) -> dict[str, Any]:
        existing = next((node for node in graph.nodes if node.id == node_id), None)
        if existing is None:
            raise NotFoundError(f'Unknown node `{node_id}`.')
        stale_roots = sorted({edge.target_node for edge in graph.edges if edge.source_node == node_id})
        graph.nodes = [node for node in graph.nodes if node.id != node_id]
        graph.edges = [edge for edge in graph.edges if edge.source_node != node_id and edge.target_node != node_id]
        graph.layout = [entry for entry in graph.layout if entry.node_id != node_id]
        return {
            'node_id': node_id,
            'delete_notebook_file': existing.kind == NodeKind.NOTEBOOK,
            'stale_roots': stale_roots,
        }

    def _validate_graph(self, graph: GraphData) -> None:
        validate_unique_node_ids(graph.nodes)
        validate_unique_edge_ids(graph.edges)
        validate_unique_target_ports(graph.edges)
        validate_acyclic(graph.nodes, graph.edges)

    @staticmethod
    def _layout_entry(node_id: str, operation: dict[str, Any]) -> LayoutEntry:
        return LayoutEntry(
            node_id=node_id,
            x=int(operation.get('x', 80)),
            y=int(operation.get('y', 80)),
            w=int(operation.get('w', 320)),
            h=int(operation.get('h', 220)),
        )


def _port_data_type(ports: list[dict[str, Any]], name: str) -> str | None:
    for port in ports:
        if port['name'] == name:
            return str(port['data_type'])
    return None


def _normalize_node_id_prefix(value: Any) -> str:
    if value is None:
        return ''
    normalized = re.sub(r'[^a-z0-9_]+', '_', str(value).strip().lower())
    normalized = re.sub(r'_+', '_', normalized).strip('_')
    return f'{normalized}_' if normalized else ''


def _normalize_title_prefix(value: Any) -> str:
    if value is None:
        return ''
    normalized = str(value).strip()
    return f'{normalized} ' if normalized else ''


def _apply_title_prefix(title: str, prefix: str) -> str:
    return f'{prefix}{title}' if prefix else title


def _coerce_organizer_ports(raw_ports: Any) -> list[dict[str, str]]:
    if not isinstance(raw_ports, list):
        raise GraphValidationError('Organizer ports must be a list.')
    ports: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_port in raw_ports:
        if not isinstance(raw_port, dict):
            raise GraphValidationError('Organizer ports must be objects.')
        key = str(raw_port.get('key') or '').strip()
        name = str(raw_port.get('name') or '').strip()
        data_type = str(raw_port.get('data_type') or '').strip()
        if not key or not name or not data_type:
            raise GraphValidationError('Organizer ports must define `key`, `name`, and `data_type`.')
        if key in seen:
            raise GraphValidationError(f'Organizer port key `{key}` is duplicated.')
        seen.add(key)
        ports.append({'key': key, 'name': name, 'data_type': data_type})
    return ports


AREA_TITLE_POSITIONS = {
    'top-left',
    'top-center',
    'top-right',
    'right-center',
    'bottom-right',
    'bottom-center',
    'bottom-left',
    'left-center',
}

AREA_COLOR_KEYS = {
    'red',
    'orange',
    'yellow',
    'green',
    'blue',
    'purple',
    'white',
    'black',
}


def _coerce_area_ui(raw: Any) -> dict[str, Any]:
    ui = raw if isinstance(raw, dict) else {}
    title_position = str(ui.get('title_position') or 'top-left').strip()
    if title_position not in AREA_TITLE_POSITIONS:
        title_position = 'top-left'
    area_color = str(ui.get('area_color') or 'blue').strip()
    if area_color not in AREA_COLOR_KEYS:
        area_color = 'blue'
    return {
        'hidden_inputs': [],
        'frozen': bool(ui.get('frozen', False)),
        'title_position': title_position,
        'area_color': area_color,
        'area_filled': bool(ui.get('area_filled', True)),
    }
