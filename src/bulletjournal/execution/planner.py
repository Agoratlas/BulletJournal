from __future__ import annotations

from collections import defaultdict, deque

from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.models import Edge, GraphData


def dependency_maps(graph: GraphData) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    upstream: dict[str, set[str]] = defaultdict(set)
    downstream: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        upstream[edge.target_node].add(edge.source_node)
        downstream[edge.source_node].add(edge.target_node)
    return upstream, downstream


def topological_nodes(graph: GraphData) -> list[str]:
    upstream, downstream = dependency_maps(graph)
    node_ids = {node.id for node in graph.nodes}
    indegree = {node_id: len(upstream.get(node_id, set())) for node_id in node_ids}
    queue = deque(sorted(node_id for node_id, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for child in sorted(downstream.get(node_id, set())):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return ordered


def upstream_closure(graph: GraphData, node_id: str) -> list[str]:
    upstream, _ = dependency_maps(graph)
    visited: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for parent in upstream.get(current, set()):
            if parent not in visited:
                visited.add(parent)
                stack.append(parent)
    ordered = topological_nodes(graph)
    return [candidate for candidate in ordered if candidate in visited]


def downstream_closure(graph: GraphData, node_id: str) -> list[str]:
    _, downstream = dependency_maps(graph)
    visited: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for child in downstream.get(current, set()):
            if child not in visited:
                visited.add(child)
                stack.append(child)
    ordered = topological_nodes(graph)
    return [candidate for candidate in ordered if candidate in visited]


def run_plan_for_node(graph: GraphData, node_id: str, upstream_node_ids: list[str] | None = None) -> list[str]:
    requested = set(upstream_node_ids or []) | {node_id}
    ordered = topological_nodes(graph)
    return [candidate for candidate in ordered if candidate in requested]


def stale_or_pending_nodes(
    graph: GraphData, artifact_heads: list[dict[str, object]], *, include_file_inputs: bool = False
) -> list[str]:
    states_by_node: dict[str, set[str]] = defaultdict(set)
    for head in artifact_heads:
        states_by_node[str(head['node_id'])].add(str(head['state']))
    node_map = {node.id: node for node in graph.nodes}
    ordered = topological_nodes(graph)
    selected: list[str] = []
    for node_id in ordered:
        node = node_map[node_id]
        if node.kind == NodeKind.FILE_INPUT and not include_file_inputs:
            continue
        states = states_by_node.get(node_id, set())
        if ArtifactState.PENDING.value in states or ArtifactState.STALE.value in states:
            selected.append(node_id)
    return selected


def visible_edge_id(edge: Edge) -> str:
    return f'{edge.source_node}.{edge.source_port}__{edge.target_node}.{edge.target_port}'
