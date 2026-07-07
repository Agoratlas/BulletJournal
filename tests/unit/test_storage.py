import json
from pathlib import Path

import pandas as pd
import pytest

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.domain.models import Edge, LayoutEntry, Node
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import init_project_root, require_project_root


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
    assert project_json['schema_version'] == 2
    assert paths.object_store_dir == paths.root / 'objects'


def test_layout_only_init_creates_schema_v2_files_and_directories(tmp_path) -> None:
    paths = init_project_root(
        tmp_path / 'study-a',
        title='Study A',
        project_id='study-a',
        initialize_environment=False,
    )

    graph_meta = json.loads((paths.graph_dir / 'meta.json').read_text(encoding='utf-8'))
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))

    assert graph_meta['schema_version'] == 1
    assert graph_meta['project_id'] == 'study-a'
    assert project_json['schema_version'] == 2
    assert project_json['project_id'] == 'study-a'
    assert project_json['title'] == 'Study A'
    assert paths.graph_dir.is_dir()
    assert paths.metadata_dir.is_dir()
    assert paths.object_store_dir.is_dir()
    assert paths.dashboards_dir.is_dir()
    assert paths.temp_dir.is_dir()
    assert paths.uploads_dir.is_dir()
    assert paths.pulled_files_dir.is_dir()
    assert paths.execution_logs_dir.is_dir()
    assert paths.worker_temp_dir.is_dir()
    assert paths.state_db_path.is_file()


def test_layout_only_init_does_not_create_environment_files(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'study-a', project_id='study-a', initialize_environment=False)

    assert paths.pyproject_path.exists() is False
    assert paths.uv_lock_path.exists() is False


def test_layout_only_init_does_not_overwrite_existing_environment_files(tmp_path) -> None:
    project_root = tmp_path / 'study-a'
    project_root.mkdir()
    pyproject_path = project_root / 'pyproject.toml'
    uv_lock_path = project_root / 'uv.lock'
    pyproject_path.write_text('[project]\nname = "external"\n', encoding='utf-8')
    uv_lock_path.write_text('version = 1\n', encoding='utf-8')

    paths = init_project_root(project_root, project_id='study-a', initialize_environment=False)

    assert pyproject_path.read_text(encoding='utf-8') == '[project]\nname = "external"\n'
    assert uv_lock_path.read_text(encoding='utf-8') == 'version = 1\n'
    assert paths.pyproject_path == pyproject_path
    assert paths.uv_lock_path == uv_lock_path


def test_layout_only_init_is_idempotent(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'study-a', title='Study A', project_id='study-a', initialize_environment=False)
    initial_graph_meta = (paths.graph_dir / 'meta.json').read_text(encoding='utf-8')
    initial_project_json = paths.project_json_path.read_text(encoding='utf-8')

    repeated = init_project_root(
        paths.root, title='Different Title', project_id='study-a', initialize_environment=False
    )

    assert (repeated.graph_dir / 'meta.json').read_text(encoding='utf-8') == initial_graph_meta
    assert repeated.project_json_path.read_text(encoding='utf-8') == initial_project_json


def test_layout_only_init_repairs_missing_transient_directories(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'study-a', project_id='study-a', initialize_environment=False)
    paths.dashboards_dir.rmdir()
    paths.execution_logs_dir.rmdir()

    repaired = init_project_root(paths.root, project_id='study-a', initialize_environment=False)

    assert repaired.dashboards_dir.is_dir()
    assert repaired.execution_logs_dir.is_dir()


def test_layout_only_init_fails_on_invalid_existing_schema_version(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'study-a', project_id='study-a', initialize_environment=False)
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))
    project_json['schema_version'] = 1
    paths.project_json_path.write_text(json.dumps(project_json), encoding='utf-8')

    with pytest.raises(ProjectValidationError, match='Schema version 1 projects are no longer supported'):
        init_project_root(paths.root, project_id='study-a', initialize_environment=False)


def test_require_project_root_rejects_schema_version_1(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))
    project_json['schema_version'] = 1
    paths.project_json_path.write_text(json.dumps(project_json), encoding='utf-8')

    with pytest.raises(ProjectValidationError, match='Schema version 1 projects are no longer supported'):
        require_project_root(paths.root)


def test_object_store_persists_dataframe(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)
    frame = pd.DataFrame({'a': [1, 2]})

    persisted = store.persist_value(frame, 'pandas.DataFrame')
    loaded = store.load_value(persisted['artifact_hash'], 'pandas.DataFrame')

    assert loaded.equals(frame)


def test_object_store_allows_empty_optional_artifacts(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)

    persisted = store.persist_value(None, 'int')
    loaded = store.load_value(persisted['artifact_hash'], 'int')

    assert loaded is None
    assert persisted['preview'] == {'kind': 'empty'}


def test_object_store_rejects_wrong_export_type(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)

    with pytest.raises(TypeError, match='Artifact export type mismatch: expected int, got str'):
        store.persist_value('not-an-int', 'int')


def test_object_store_rejects_wrong_import_type(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)
    persisted = store.persist_value('hello', 'str')

    with pytest.raises(TypeError, match='Artifact import type mismatch: expected int, got str'):
        store.load_value(persisted['artifact_hash'], 'int')


def test_object_store_persists_round_float_values_from_ints(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    store = ObjectStore(paths)

    persisted = store.persist_value(3.0, 'float')
    loaded = store.load_value(persisted['artifact_hash'], 'float')

    assert loaded == 3.0
    assert isinstance(loaded, float)
    assert persisted['preview']['repr'] == '3.0'


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
