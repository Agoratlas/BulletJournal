from __future__ import annotations

from typing import Any

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
from bulletjournal.execution.planner import downstream_closure, visible_edge_id


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
        active_run_interruption = None
        reparse_all = False
        stale_roots: set[str] = set()
        pending_notebook_creates: list[tuple[str, str]] = []
        pending_notebook_deletes: list[str] = []
        pending_file_input_heads: list[str] = []
        pending_state_deletes: list[str] = []
        for operation in operations:
            op_type = operation['type']
            if op_type == 'add_notebook_node':
                node_id, source = self._add_notebook_node(graph, operation)
                pending_notebook_creates.append((node_id, source))
                reparse_all = True
            elif op_type == 'add_file_input_node':
                node_id = self._add_file_input_node(graph, operation)
                pending_file_input_heads.append(node_id)
            elif op_type == 'add_edge':
                self._add_edge(graph, operation)
                stale_roots.add(str(operation['target_node']))
            elif op_type == 'remove_edge':
                edge = self._remove_edge(graph, str(operation['edge_id']))
                if edge is not None:
                    stale_roots.add(edge.target_node)
            elif op_type == 'update_node_layout':
                self._update_layout(graph, operation)
            elif op_type == 'update_node_title':
                self._update_title(graph, operation)
            elif op_type == 'update_node_hidden_inputs':
                self._update_hidden_inputs(graph, operation)
            elif op_type == 'delete_node':
                deleted = self._delete_node(graph, str(operation['node_id']))
                stale_roots.update(deleted['stale_roots'])
                if deleted['delete_notebook_file']:
                    pending_notebook_deletes.append(str(deleted['node_id']))
                pending_state_deletes.append(str(deleted['node_id']))
                reparse_all = True
            else:
                raise GraphValidationError(f'Unsupported graph operation `{op_type}`.')
        self._validate_graph(graph)
        if operations and self.project_service.run_service is not None:
            active_run_interruption = self.project_service.run_service.interrupt_active_run_for_graph_edit()
        graph = self.project_service.write_graph(graph)
        for node_id, source in pending_notebook_creates:
            self.project_service.require_project().paths.notebook_path(node_id).write_text(source, encoding='utf-8')
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
    ) -> list[str]:
        graph = self.project_service.graph()
        kept_edges: list[Edge] = []
        removed: list[str] = []
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
                removed.append(edge.id)
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

    def _add_notebook_node(self, graph: GraphData, operation: dict[str, Any]) -> tuple[str, str]:
        node_id = str(operation['node_id'])
        title = str(operation['title'])
        if any(node.id == node_id for node in graph.nodes):
            raise GraphValidationError(f'Node `{node_id}` already exists.')
        template_ref = operation.get('template_ref')
        source_text = operation.get('source_text')
        ui = operation.get('ui')
        template = self.project_service.template_service.resolve_template_source(str(template_ref)) if template_ref else None
        node = Node(
            id=node_id,
            kind=NodeKind.NOTEBOOK,
            title=title,
            path=self.project_service.require_project().paths.notebook_relpath(node_id),
            template=None if template_ref is None or source_text is not None else self.project_service.template_service.template_ref(str(template_ref)),
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
        graph.nodes.append(Node(id=node_id, kind=NodeKind.FILE_INPUT, title=title, ui={'hidden_inputs': [], 'artifact_name': artifact_name}))
        graph.layout.append(self._layout_entry(node_id, operation))
        return node_id

    def _add_edge(self, graph: GraphData, operation: dict[str, Any]) -> None:
        source_node = str(operation['source_node'])
        source_port = str(operation['source_port'])
        target_node = str(operation['target_node'])
        target_port = str(operation['target_port'])
        node_ids = {node.id for node in graph.nodes}
        assert_node_exists(node_ids, source_node)
        assert_node_exists(node_ids, target_node)
        source_interface = self.project_service.latest_interface(source_node)
        target_interface = self.project_service.latest_interface(target_node)
        if source_interface is None or target_interface is None:
            raise GraphValidationError('Cannot connect nodes without parsed interfaces.')
        source_type = _port_data_type(source_interface.get('outputs', []) + source_interface.get('assets', []), source_port)
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

    def _update_hidden_inputs(self, graph: GraphData, operation: dict[str, Any]) -> None:
        node_id = str(operation['node_id'])
        hidden_inputs_raw = operation.get('hidden_inputs', [])
        if not isinstance(hidden_inputs_raw, list):
            raise GraphValidationError('Hidden inputs must be a list of port names.')
        hidden_inputs = sorted({str(item) for item in hidden_inputs_raw})
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            raise GraphValidationError('Cannot configure hidden inputs before the notebook interface is parsed.')
        inputs_by_name = {str(port['name']): port for port in interface.get('inputs', [])}
        for hidden_input in hidden_inputs:
            port = inputs_by_name.get(hidden_input)
            if port is None:
                raise GraphValidationError(f'Hidden input `{hidden_input}` does not exist on node `{node_id}`.')
            if not bool(port.get('has_default', False)):
                raise GraphValidationError(
                    f'Hidden input `{hidden_input}` on node `{node_id}` must declare a default value.'
                )
        for node in graph.nodes:
            if node.id == node_id:
                node.ui = {**node.ui, 'hidden_inputs': hidden_inputs}
                return
        raise GraphValidationError(f'Unknown node `{node_id}`.')

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
