from __future__ import annotations

from collections import defaultdict, deque

from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.domain.models import Edge, Node


def validate_unique_node_ids(nodes: list[Node]) -> None:
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            raise GraphValidationError(f'duplicate node id: {node.id}')
        seen.add(node.id)


def validate_unique_edge_ids(edges: list[Edge]) -> None:
    seen: set[str] = set()
    for edge in edges:
        if edge.id in seen:
            raise GraphValidationError(f'duplicate edge id: {edge.id}')
        seen.add(edge.id)


def assert_node_exists(node_ids: set[str], node_id: str) -> None:
    if node_id not in node_ids:
        raise GraphValidationError(f'unknown node: {node_id}')


def validate_unique_target_ports(edges: list[Edge]) -> None:
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        key = (edge.target_node, edge.target_port)
        if key in seen:
            raise GraphValidationError(f'duplicate input binding for {edge.target_node}.{edge.target_port}')
        seen.add(key)


def validate_acyclic(nodes: list[Node], edges: list[Edge]) -> None:
    node_ids = {node.id for node in nodes}
    indegree = {node_id: 0 for node_id in node_ids}
    children: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.target_node not in node_ids or edge.source_node not in node_ids:
            continue
        if edge.target_node not in children[edge.source_node]:
            children[edge.source_node].add(edge.target_node)
            indegree[edge.target_node] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for child in sorted(children.get(node_id, set())):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(node_ids):
        raise GraphValidationError('graph must be acyclic')
