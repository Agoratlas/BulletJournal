from __future__ import annotations

from typing import Any

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import GraphData, Node, NotebookInterface, Port


def organizer_ports_from_ui(ui: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(ui, dict):
        return []
    raw_ports = ui.get('organizer_ports')
    if not isinstance(raw_ports, list):
        return []
    ports: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_port in raw_ports:
        if not isinstance(raw_port, dict):
            continue
        key = str(raw_port.get('key') or '').strip()
        name = str(raw_port.get('name') or '').strip()
        data_type = str(raw_port.get('data_type') or '').strip()
        if not key or not name or not data_type or key in seen:
            continue
        seen.add(key)
        ports.append({'key': key, 'name': name, 'data_type': data_type})
    return ports


def organizer_interface_for_ports(*, node_id: str, ports: list[dict[str, str]]) -> NotebookInterface:
    inputs = [
        Port(name=port['key'], label=port['name'], data_type=port['data_type'], direction='input') for port in ports
    ]
    outputs = [
        Port(name=port['key'], label=port['name'], data_type=port['data_type'], direction='output') for port in ports
    ]
    return NotebookInterface(
        node_id=node_id,
        source_hash='organizer',
        inputs=inputs,
        outputs=outputs,
        assets=[],
        docs='Organizer block. Each input is forwarded directly to the matching output.',
        issues=[],
    )


def organizer_interface_for_node(node: Node) -> NotebookInterface:
    return organizer_interface_for_ports(node_id=node.id, ports=organizer_ports_from_ui(node.ui))


def resolve_input_binding(graph: GraphData, *, node_id: str, input_name: str) -> tuple[str, str] | None:
    edge = next(
        (item for item in graph.edges if item.target_node == node_id and item.target_port == input_name),
        None,
    )
    if edge is None:
        return None
    return resolve_output_binding(graph, source_node=edge.source_node, source_port=edge.source_port)


def resolve_output_binding(graph: GraphData, *, source_node: str, source_port: str) -> tuple[str, str] | None:
    node_by_id = {node.id: node for node in graph.nodes}
    current_node = source_node
    current_port = source_port
    visited: set[tuple[str, str]] = set()
    while True:
        state = (current_node, current_port)
        if state in visited:
            return None
        visited.add(state)
        node = node_by_id.get(current_node)
        if node is None or node.kind != NodeKind.ORGANIZER:
            return current_node, current_port
        passthrough = next(
            (edge for edge in graph.edges if edge.target_node == current_node and edge.target_port == current_port),
            None,
        )
        if passthrough is None:
            return None
        current_node = passthrough.source_node
        current_port = passthrough.source_port
