from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import Edge, GraphData, LayoutEntry, Node
from bulletjournal.execution.planner import (
    dependency_maps,
    downstream_closure,
    run_plan_for_node,
    stale_or_pending_nodes,
    topological_nodes,
    upstream_closure,
)


def _graph() -> GraphData:
    return GraphData(
        meta={'graph_version': 1},
        nodes=[
            Node(id='input_file', kind=NodeKind.CONSTANT, title='Input File', ui={'data_type': 'file'}),
            Node(id='source', kind=NodeKind.NOTEBOOK, title='Source'),
            Node(id='middle', kind=NodeKind.NOTEBOOK, title='Middle'),
            Node(id='leaf', kind=NodeKind.NOTEBOOK, title='Leaf'),
        ],
        edges=[
            Edge(
                id='input_file.file__source.file',
                source_node='input_file',
                source_port='file',
                target_node='source',
                target_port='file',
            ),
            Edge(
                id='source.out__middle.in',
                source_node='source',
                source_port='out',
                target_node='middle',
                target_port='in',
            ),
            Edge(
                id='middle.out__leaf.in', source_node='middle', source_port='out', target_node='leaf', target_port='in'
            ),
        ],
        layout=[LayoutEntry(node_id='input_file', x=0, y=0, w=320, h=200)],
    )


def test_dependency_maps_and_topological_order_are_deterministic() -> None:
    graph = _graph()

    upstream, downstream = dependency_maps(graph)

    assert upstream['source'] == {'input_file'}
    assert upstream['middle'] == {'source'}
    assert upstream['leaf'] == {'middle'}
    assert downstream['input_file'] == {'source'}
    assert downstream['source'] == {'middle'}
    assert downstream['middle'] == {'leaf'}
    assert topological_nodes(graph) == ['input_file', 'source', 'middle', 'leaf']


def test_upstream_and_downstream_closures_follow_graph_order() -> None:
    graph = _graph()

    assert upstream_closure(graph, 'leaf') == ['input_file', 'source', 'middle']
    assert upstream_closure(graph, 'source') == ['input_file']
    assert downstream_closure(graph, 'source') == ['middle', 'leaf']
    assert downstream_closure(graph, 'leaf') == []


def test_run_plan_for_node_includes_requested_upstream_nodes_once() -> None:
    graph = _graph()

    plan = run_plan_for_node(graph, 'leaf', upstream_node_ids=['input_file', 'source', 'middle'])

    assert plan == ['input_file', 'source', 'middle', 'leaf']


def test_stale_or_pending_nodes_excludes_constants_by_default() -> None:
    graph = _graph()
    artifact_heads = [
        {'node_id': 'input_file', 'state': 'pending'},
        {'node_id': 'source', 'state': 'ready'},
        {'node_id': 'middle', 'state': 'stale'},
        {'node_id': 'leaf', 'state': 'pending'},
    ]

    selected = stale_or_pending_nodes(graph, artifact_heads)
    selected_with_files = stale_or_pending_nodes(graph, artifact_heads, include_file_inputs=True)

    assert selected == ['middle', 'leaf']
    assert selected_with_files == ['input_file', 'middle', 'leaf']
