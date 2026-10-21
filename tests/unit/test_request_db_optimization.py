from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.notebook_service import NotebookService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB


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
