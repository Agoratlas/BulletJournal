from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bulletjournal.services.dashboard_service import DashboardService
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.notebook_service import NotebookService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB


@pytest.mark.parametrize('journal_mode', ['DELETE', 'WAL'])
@pytest.mark.parametrize('node_count', [1, 12])
def test_mass_delete_has_constant_commits_and_restorable_manifests(tmp_path, monkeypatch, journal_mode, node_count):
    import json
    import sqlite3

    from bulletjournal.domain.hashing import hash_json

    monkeypatch.setenv('BULLETJOURNAL_DB_JOURNAL_MODE', journal_mode)
    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'add_area_node', 'node_id': f'area_{index}'} for index in range(node_count)],
    )
    project = service.require_project()
    statements = []
    original = project.state_db._connect

    def connect():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(project.state_db, '_connect', connect)
    result = graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'delete_node', 'node_id': f'area_{index}'} for index in range(node_count)],
        request_id='delete-many',
    )
    # Two tombstone phases, two activity metadata writes, cached response.
    assert statements.count('COMMIT') == 5
    assert len(result['tombstone_mutations']) == node_count
    assert not service.graph().nodes
    for mutation in result['tombstone_mutations']:
        with sqlite3.connect(project.state_db.path) as connection:
            checksum, path = connection.execute(
                'SELECT manifest_checksum, manifest_path FROM node_tombstones WHERE tombstone_id = ?',
                (mutation['tombstone_id'],),
            ).fetchone()
        envelope = json.loads((project.paths.root / path).read_text())
        assert envelope['checksum'] == checksum == hash_json(envelope['manifest'])
        graph.restore_tombstone(mutation['tombstone_id'])
    assert len(service.graph().nodes) == node_count


def test_failed_mass_delete_does_not_publish_graph_or_finalize_any_checksums(tmp_path, monkeypatch):
    import sqlite3

    import bulletjournal.services.graph_service as module

    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'add_area_node', 'node_id': f'area_{index}'} for index in range(3)],
    )
    original = module.atomic_write_text
    writes = []

    def fail_second_manifest(path, text):
        writes.append(path)
        if len(writes) == 2:
            raise OSError('manifest disk failure')
        original(path, text)

    monkeypatch.setattr(module, 'atomic_write_text', fail_second_manifest)
    with pytest.raises(OSError, match='manifest disk failure'):
        graph.apply_operations(
            int(service.graph().meta['graph_version']),
            [{'type': 'delete_node', 'node_id': f'area_{index}'} for index in range(3)],
        )
    assert len(service.graph().nodes) == 3
    with sqlite3.connect(service.require_project().state_db.path) as connection:
        assert connection.execute('SELECT manifest_checksum FROM node_tombstones').fetchall() == [
            ('preparing',),
            ('preparing',),
            ('preparing',),
        ]


@pytest.mark.parametrize('failure_phase', ['retain', 'checksum'])
def test_tombstone_batch_database_failure_rolls_back_entire_phase(tmp_path, monkeypatch, failure_phase):
    import sqlite3

    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'add_area_node', 'node_id': f'area_{index}'} for index in range(3)],
    )
    db = service.require_project().state_db
    method = 'tombstone_node_state' if failure_phase == 'retain' else 'update_tombstone_manifest_checksum'
    original = getattr(db, method)
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(1)
        result = original(*args, **kwargs)
        if len(calls) == 2:
            raise sqlite3.OperationalError('injected database failure')
        return result

    monkeypatch.setattr(db, method, fail_second)
    with pytest.raises(sqlite3.OperationalError, match='injected database failure'):
        graph.apply_operations(
            int(service.graph().meta['graph_version']),
            [{'type': 'delete_node', 'node_id': f'area_{index}'} for index in range(3)],
        )
    assert len(service.graph().nodes) == 3
    with sqlite3.connect(db.path) as connection:
        rows = connection.execute('SELECT manifest_checksum FROM node_tombstones').fetchall()
        assert rows == ([] if failure_phase == 'retain' else [('preparing',)] * 3)
        statuses = connection.execute('SELECT DISTINCT status FROM node_incarnations').fetchall()
        assert statuses == [('live' if failure_phase == 'retain' else 'tombstoned',)]


def test_batch_tombstone_pins_are_committed_before_manifest_publication(tmp_path, monkeypatch):
    import json
    import sqlite3

    import bulletjournal.services.graph_service as module

    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [
            {'type': 'add_constant_node', 'node_id': f'constant_{i}', 'data_type': 'int', 'value': i + 1}
            for i in range(3)
        ],
    )
    project = service.require_project()
    original = module.atomic_write_text
    publications = []

    def inspect_manifest(path, text):
        envelope = json.loads(text)
        with sqlite3.connect(project.state_db.path) as connection:
            assert connection.execute('SELECT COUNT(*) FROM node_tombstones').fetchone()[0] == 3
            assert (
                connection.execute("SELECT COUNT(*) FROM object_pins WHERE owner_kind = 'tombstone'").fetchone()[0] == 3
            )
            assert connection.execute('SELECT DISTINCT manifest_checksum FROM node_tombstones').fetchall() == [
                ('preparing',)
            ]
        assert len(envelope['manifest']['state']['pinned_hashes']) == 1
        publications.append(path)
        original(path, text)

    monkeypatch.setattr(module, 'atomic_write_text', inspect_manifest)
    result = graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'delete_node', 'node_id': f'constant_{i}'} for i in range(3)],
    )
    assert len(publications) == 3
    monkeypatch.setattr(module, 'atomic_write_text', original)
    for mutation in result['tombstone_mutations']:
        graph.restore_tombstone(mutation['tombstone_id'])
    assert all(project.state_db.get_artifact_head(f'constant_{i}', 'value')['current_version_id'] for i in range(3))


class Events:
    def publish(self, *args, **kwargs):
        pass


def test_snapshot_connection_count_does_not_grow_with_notebooks(tmp_path, monkeypatch):
    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    for index in range(12):
        graph.apply_operations(
            int(service.graph().meta['graph_version']),
            [{'type': 'add_notebook_node', 'node_id': f'notebook_{index}', 'title': 'Notebook'}],
        )
    db = service.require_project().state_db
    original = db._connect
    calls = []

    def connect():
        calls.append(1)
        return original()

    monkeypatch.setattr(db, '_connect', connect)
    snapshot = service.snapshot()
    assert len(snapshot['graph']['nodes']) == 12
    assert all(node['interface'] is not None for node in snapshot['graph']['nodes'])
    assert len(calls) <= 15


@pytest.mark.parametrize('include_details', [True, False])
def test_dashboard_snapshot_reuses_graph_and_asset_states(tmp_path, monkeypatch, include_details):
    import json

    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph_service = GraphService(service)
    graph_service.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'add_dashboard_node', 'node_id': f'dashboard_{index}'} for index in range(12)],
    )
    project = service.require_project()
    service.dashboard_service = DashboardService(service)
    for index in range(12):
        project.paths.dashboard_path(f'dashboard_{index}').write_text(
            json.dumps(
                {
                    'title': 'Dashboard',
                    'sources': [{'node_id': 'source'}],
                    'panels': [
                        {'node_id': 'source', 'asset_name': 'ready'},
                        {'node_id': 'source', 'asset_name': 'missing'},
                    ],
                }
            ),
            encoding='utf-8',
        )
    from bulletjournal.domain.enums import ArtifactState

    project.state_db.ensure_asset_head('source', 'ready', ArtifactState.READY)
    connections, graph_reads = [], []
    connect, read = project.state_db._connect, project.graph_store.read

    def counted_connect():
        connections.append(1)
        return connect()

    def counted_read():
        graph_reads.append(1)
        return read()

    monkeypatch.setattr(project.state_db, '_connect', counted_connect)
    monkeypatch.setattr(project.graph_store, 'read', counted_read)
    snapshot = service.snapshot(include_details=include_details)
    assert len(graph_reads) == 1
    assert len(connections) <= 15
    for node in snapshot['graph']['nodes']:
        assert node['ui']['asset_counts'] == {'pending': 1, 'stale': 0, 'ready': 1}
        assert node['ui']['source_count'] == 1
        assert node['ui']['panel_count'] == 2


@pytest.mark.parametrize('journal_mode', ['DELETE', 'WAL'])
def test_transaction_batches_commits_and_rolls_back(tmp_path, monkeypatch, journal_mode):
    monkeypatch.setenv('BULLETJOURNAL_DB_JOURNAL_MODE', journal_mode)
    db = StateDB(tmp_path / 'state.db')
    statements = []
    original = db._connect

    def connect():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection

    db._connect = connect
    with db.transaction():
        db.set_project_meta('first', 'one')
        db.set_project_meta('second', 'two')
        assert db.get_project_meta('first') == 'one'
    assert sum(statement == 'COMMIT' for statement in statements) == 1
    with pytest.raises(ValueError):
        with db.transaction():
            db.set_project_meta('first', 'changed')
            raise ValueError('abort')
    assert db.get_project_meta('first') == 'one'


def test_notebook_port_heads_share_one_commit(tmp_path, monkeypatch):
    service = ProjectService(Events(), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    service.watcher.stop()
    graph = GraphService(service)
    graph.apply_operations(
        int(service.graph().meta['graph_version']),
        [{'type': 'add_notebook_node', 'node_id': 'notebook', 'title': 'Notebook'}],
    )
    db = service.require_project().state_db
    original = db._connect
    statements = []

    def connect():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(db, '_connect', connect)
    NotebookService(service)._sync_ports(
        'notebook',
        None,
        SimpleNamespace(inputs=[], outputs=[SimpleNamespace(name=name) for name in ('first', 'second', 'third')]),
    )
    assert sum(statement == 'COMMIT' for statement in statements) == 1
    assert {head['artifact_name'] for head in db.list_artifact_heads()} >= {'first', 'second', 'third'}


def test_transaction_is_thread_local(tmp_path):
    db = StateDB(tmp_path / 'state.db')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction():
            db.set_project_meta('uncommitted', 'value')
            assert pool.submit(db.get_project_meta, 'uncommitted').result(timeout=5) is None
    assert db.get_project_meta('uncommitted') == 'value'


def test_bulk_interfaces_match_single_reads_across_incarnations(tmp_path):
    db = StateDB(tmp_path / 'state.db')
    db.save_notebook_revision('legacy', 'old', None, {'source_hash': 'old'})
    db.save_notebook_contract('legacy', 'latest', None, {'interface': {'source_hash': 'latest'}})
    db.register_node_incarnation('incarnation-old', 'reused', 'notebook')
    db.save_notebook_revision('reused', 'retired', None, {'source_hash': 'retired'})
    with db._connection() as connection:
        connection.execute("UPDATE node_incarnations SET status = 'tombstoned' WHERE node_id = 'reused'")
    db.register_node_incarnation('incarnation-new', 'reused', 'notebook')
    assert 'reused' not in db.latest_interfaces_json()
    db.save_notebook_contract('reused', 'new', None, {'interface': {'source_hash': 'new'}})
    assert db.latest_interfaces_json() == {
        node_id: db.latest_interface_json(node_id) for node_id in ('legacy', 'reused')
    }
