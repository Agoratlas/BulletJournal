import threading

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import GraphData, LayoutEntry, Node
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.project_fs import init_project_root


def test_graph_store_read_and_write_are_serialized(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = GraphStore(paths)
    initial = store.read()

    started = threading.Event()
    release = threading.Event()
    original_replace = store._atomic_replace_graph_dir

    def delayed_replace(*, meta, nodes, edges, layout):
        started.set()
        release.wait(timeout=5)
        original_replace(meta=meta, nodes=nodes, edges=edges, layout=layout)

    store._atomic_replace_graph_dir = delayed_replace  # type: ignore[method-assign]
    results: dict[str, object] = {}

    def writer() -> None:
        graph = GraphData(
            meta=dict(initial.meta), nodes=list(initial.nodes), edges=list(initial.edges), layout=list(initial.layout)
        )
        store.write(graph)
        results['write_done'] = True

    thread = threading.Thread(target=writer)
    thread.start()
    started.wait(timeout=5)

    def reader() -> None:
        results['graph'] = store.read()

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    release.set()
    thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert results['write_done'] is True
    assert isinstance(results['graph'], GraphData)


def test_graph_store_round_trip_preserves_organizer_ui(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = GraphStore(paths)
    graph = store.read()
    graph.nodes.append(
        Node(
            id='organizer',
            kind=NodeKind.ORGANIZER,
            title='Organizer',
            ui={
                'organizer_ports': [
                    {'key': 'dataset', 'name': 'dataset', 'data_type': 'file'},
                    {'key': 'count', 'name': 'sample_count', 'data_type': 'int'},
                ],
            },
        )
    )
    graph.layout.append(LayoutEntry(node_id='organizer', x=80, y=80, w=160, h=140))

    store.write(graph)
    loaded = store.read()

    organizer = next(node for node in loaded.nodes if node.id == 'organizer')
    assert organizer.ui['organizer_ports'][1]['name'] == 'sample_count'
