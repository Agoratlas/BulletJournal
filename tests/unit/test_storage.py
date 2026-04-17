import json
from pathlib import Path

import pandas as pd

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import Edge, LayoutEntry, Node
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import init_project_root


def test_project_init_and_graph_roundtrip(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    graph = GraphStore(paths).read()

    assert graph.meta['graph_version'] == 1
    assert graph.nodes == []
    assert paths.pyproject_path.is_file()
    assert paths.uv_lock_path.is_file()
    assert (paths.metadata_dir / 'environment.json').exists() is False
    assert (paths.metadata_dir / 'environment_packages.txt').exists() is False


def test_project_init_defaults_project_id_from_directory_name(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'My Study')
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))

    assert project_json['project_id'] == 'my_study'


def test_object_store_persists_dataframe(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)
    frame = pd.DataFrame({'a': [1, 2]})

    persisted = store.persist_value(frame, 'pandas.DataFrame')
    loaded = store.load_value(persisted['artifact_hash'], 'pandas.DataFrame')

    assert loaded.equals(frame)


def test_graph_store_write_sorts_nodes_edges_and_layout(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = GraphStore(paths)
    graph = store.read()
    graph.nodes = [
        Node(id='z', kind=NodeKind.NOTEBOOK, title='Z'),
        Node(id='a', kind=NodeKind.NOTEBOOK, title='A'),
    ]
    graph.edges = [
        Edge(id='z.out__a.in', source_node='z', source_port='out', target_node='a', target_port='in'),
        Edge(id='a.out__z.in', source_node='a', source_port='out', target_node='z', target_port='in'),
    ]
    graph.layout = [
        LayoutEntry(node_id='z', x=0, y=0, w=10, h=10),
        LayoutEntry(node_id='a', x=0, y=0, w=10, h=10),
    ]

    written = store.write(graph)

    assert [node.id for node in written.nodes] == ['a', 'z']
    assert [edge.id for edge in written.edges] == ['a.out__z.in', 'z.out__a.in']
    assert [entry.node_id for entry in written.layout] == ['a', 'z']
    assert written.meta['graph_version'] == 2


def test_object_store_persist_file_does_not_leave_temp_upload(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)
    temp_file = store.create_temp_file('.txt')
    temp_file.write_text('hello', encoding='utf-8')

    persisted = store.persist_file(temp_file, extension='.txt')

    assert Path(store.load_file_path(persisted['artifact_hash'])).exists()
    assert temp_file.exists()
