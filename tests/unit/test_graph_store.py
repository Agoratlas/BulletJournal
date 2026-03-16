import threading

from bulletjournal.domain.models import GraphData
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
        graph = GraphData(meta=dict(initial.meta), nodes=list(initial.nodes), edges=list(initial.edges), layout=list(initial.layout))
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
