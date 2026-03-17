import http.cookiejar
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


def test_can_add_and_run_builtin_notebook(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                    'x': 120,
                    'y': 140,
                }
            ],
        },
    )
    assert patch.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    if run.json()['status'] != 'succeeded':
        raise AssertionError(run.json())
    assert run.json()['status'] == 'succeeded'

    artifact = client.get(f'/api/v1/projects/{project_id}/artifacts/sample_node/sample_df')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'
    assert artifact.json()['data_type'] == 'pandas.DataFrame'


def test_run_upstream_executes_dependency_chain(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {'type': 'add_notebook_node', 'node_id': 'producer', 'title': 'Producer'},
                {'type': 'add_notebook_node', 'node_id': 'consumer', 'title': 'Consumer', 'x': 480, 'y': 80},
            ],
        },
    )
    assert patch.status_code == 200

    producer_source = """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    artifacts.push(4, name='number', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'
    consumer_source = """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    value = artifacts.pull(name='number', data_type=int)
    return value

@app.cell
def _(value):
    artifacts.push(value * 2, name='doubled', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'

    producer_path = project_root / 'notebooks' / 'producer.py'
    consumer_path = project_root / 'notebooks' / 'consumer.py'
    producer_path.write_text(producer_source, encoding='utf-8')
    consumer_path.write_text(consumer_source, encoding='utf-8')
    container.project_service.reparse_notebook_by_path(producer_path)
    container.project_service.reparse_notebook_by_path(consumer_path)

    graph_version = client.get(f'/api/v1/projects/{project_id}/graph').json()['meta']['graph_version']
    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'producer',
                    'source_port': 'number',
                    'target_node': 'consumer',
                    'target_port': 'number',
                }
            ],
        },
    )
    assert connect.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/consumer/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    if run.json()['status'] != 'succeeded':
        raise AssertionError(run.json())
    assert run.json()['status'] == 'succeeded'

    artifact = client.get(f'/api/v1/projects/{project_id}/artifacts/consumer/doubled')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'

    producer_path.write_text(producer_source.replace('4', '5', 1), encoding='utf-8')
    container.project_service.reparse_notebook_by_path(producer_path)
    stale = client.get(f'/api/v1/projects/{project_id}/artifacts/consumer/doubled')
    assert stale.json()['state'] == 'stale'


def test_disconnecting_edge_marks_downstream_artifact_stale_until_rerun(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'value_input.py',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'empty_notebook.py',
                    'x': 420,
                    'y': 80,
                },
            ],
        },
    )
    assert patch.status_code == 200

    graph_version = patch.json()['meta']['graph_version']
    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connect.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    artifact = client.get(f'/api/v1/projects/{project_id}/artifacts/table_sink/sample_df')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'
    assert artifact.json()['preview']['rows'] == 42

    graph_version = connect.json()['meta']['graph_version']
    disconnect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'remove_edge',
                    'edge_id': 'value_source.value__table_sink.sample_count',
                }
            ],
        },
    )
    assert disconnect.status_code == 200

    stale = client.get(f'/api/v1/projects/{project_id}/artifacts/table_sink/sample_df')
    assert stale.status_code == 200
    assert stale.json()['state'] == 'stale'
    assert stale.json()['preview']['rows'] == 42

    rerun = client.post(
        f'/api/v1/projects/{project_id}/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert rerun.status_code == 200
    assert rerun.json()['status'] == 'succeeded'

    refreshed = client.get(f'/api/v1/projects/{project_id}/artifacts/table_sink/sample_df')
    assert refreshed.status_code == 200
    assert refreshed.json()['state'] == 'ready'
    assert refreshed.json()['preview']['rows'] == 10


def test_graph_patch_failure_does_not_leave_orphan_notebook_file(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    failed = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'orphan_candidate',
                    'title': 'Orphan Candidate',
                },
                {
                    'type': 'add_edge',
                    'source_node': 'missing_source',
                    'source_port': 'value',
                    'target_node': 'orphan_candidate',
                    'target_port': 'sample_count',
                },
            ],
        },
    )
    assert failed.status_code == 409

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert all(node['id'] != 'orphan_candidate' for node in snapshot['graph']['nodes'])
    assert not (project_root / 'notebooks' / 'orphan_candidate.py').exists()


def test_graph_patch_failure_does_not_delete_existing_node_file(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'survivor',
                    'title': 'Survivor',
                }
            ],
        },
    )
    assert patch.status_code == 200
    notebook_path = project_root / 'notebooks' / 'survivor.py'
    assert notebook_path.exists()

    failed = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': patch.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'delete_node',
                    'node_id': 'survivor',
                },
                {
                    'type': 'update_node_title',
                    'node_id': 'survivor',
                    'title': 'Should Fail',
                },
            ],
        },
    )
    assert failed.status_code == 409

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    survivor = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'survivor')
    assert survivor['title'] == 'Survivor'
    assert notebook_path.exists()


def test_deleting_node_removes_artifacts_and_stales_downstream(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'value_input.py',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'empty_notebook.py',
                    'x': 420,
                    'y': 80,
                },
            ],
        },
    )
    assert patch.status_code == 200

    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': patch.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connect.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    delete = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': connect.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'delete_node',
                    'node_id': 'value_source',
                }
            ],
        },
    )
    assert delete.status_code == 200

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert 'value_source' not in node_ids
    assert 'table_sink' in node_ids
    assert all(artifact['node_id'] != 'value_source' for artifact in snapshot['artifacts'])
    assert not (project_root / 'notebooks' / 'value_source.py').exists()

    missing = client.get(f'/api/v1/projects/{project_id}/artifacts/value_source/value')
    assert missing.status_code == 404

    stale = client.get(f'/api/v1/projects/{project_id}/artifacts/table_sink/sample_df')
    assert stale.status_code == 200
    assert stale.json()['state'] == 'stale'
    assert stale.json()['preview']['rows'] == 42


def test_reparse_input_port_removal_disconnects_edges_and_stales_node_outputs(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'value_input.py',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'empty_notebook.py',
                    'x': 420,
                    'y': 80,
                },
            ],
        },
    )
    assert patch.status_code == 200

    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': patch.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connect.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    sink_path = project_root / 'notebooks' / 'table_sink.py'
    sink_source = sink_path.read_text(encoding='utf-8')
    sink_path.write_text(
        sink_source.replace(
            "sample_count = artifacts.pull(name='sample_count', data_type=int, default=10)",
            "fallback_count = artifacts.pull(name='fallback_count', data_type=int, default=10)",
        ).replace('sample_count', 'fallback_count'),
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(sink_path)

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert snapshot['graph']['edges'] == []

    stale = client.get(f'/api/v1/projects/{project_id}/artifacts/table_sink/sample_df')
    assert stale.status_code == 200
    assert stale.json()['state'] == 'stale'
    assert stale.json()['preview']['rows'] == 42

    warning_codes = {notice['code'] for notice in snapshot['notices']}
    assert 'edges_removed_for_port_change' in warning_codes


def test_notebook_edit_interrupts_active_run_and_records_dismissible_warning(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'slow_node',
                    'title': 'Slow Node',
                }
            ],
        },
    )
    assert patch.status_code == 200

    notebook_path = project_root / 'notebooks' / 'slow_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts
    import time

@app.cell
def _(time):
    time.sleep(5)
    artifacts.push(1, name='value', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    with TestClient(app) as threaded_client:
        run_response: dict[str, object] = {}

        def run_node_request() -> None:
            response = threaded_client.post(
                f'/api/v1/projects/{project_id}/nodes/slow_node/run',
                json={'mode': 'run_stale', 'action': 'use_stale'},
            )
            run_response['status_code'] = response.status_code
            run_response['body'] = response.json()

        thread = threading.Thread(target=run_node_request)
        thread.start()

        for _ in range(40):
            if container.run_service._active_run is not None:
                break
            time.sleep(0.1)
        else:
            raise AssertionError('Expected a managed run to become active.')

        notebook_path.write_text(
            notebook_path.read_text(encoding='utf-8').replace(
                "artifacts.push(1, name='value', data_type=int, is_output=True)",
                "artifacts.push(2, name='value', data_type=int, is_output=True)",
            ),
            encoding='utf-8',
        )
        container.project_service.reparse_notebook_by_path(notebook_path)

        thread.join(timeout=10)

    assert run_response['status_code'] == 200
    assert isinstance(run_response['body'], dict)
    assert run_response['body']['status'] == 'cancelled'

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    warning = next(notice for notice in snapshot['notices'] if notice['code'] == 'run_interrupted_by_graph_edit')
    assert warning['severity'] == 'warning'

    dismissed = client.post(f"/api/v1/projects/{project_id}/notices/{warning['issue_id']}/dismiss")
    assert dismissed.status_code == 200

    refreshed = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert all(notice['issue_id'] != warning['issue_id'] for notice in refreshed['notices'])


def test_cosmetic_graph_edit_does_not_interrupt_active_run(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'slow_node',
                    'title': 'Slow Node',
                    'x': 100,
                    'y': 100,
                }
            ],
        },
    )
    assert patch.status_code == 200

    notebook_path = project_root / 'notebooks' / 'slow_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts
    import time

@app.cell
def _(time):
    time.sleep(2)
    artifacts.push(1, name='value', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    with TestClient(app) as threaded_client:
        run_response: dict[str, object] = {}

        def run_node_request() -> None:
            response = threaded_client.post(
                f'/api/v1/projects/{project_id}/nodes/slow_node/run',
                json={'mode': 'run_stale', 'action': 'use_stale'},
            )
            run_response['status_code'] = response.status_code
            run_response['body'] = response.json()

        thread = threading.Thread(target=run_node_request)
        thread.start()

        for _ in range(40):
            if container.run_service._active_run is not None:
                break
            time.sleep(0.1)
        else:
            raise AssertionError('Expected a managed run to become active.')

        edited = threaded_client.patch(
            f'/api/v1/projects/{project_id}/graph',
            json={
                'graph_version': threaded_client.get(f'/api/v1/projects/{project_id}/graph').json()['meta']['graph_version'],
                'operations': [
                    {
                        'type': 'update_node_title',
                        'node_id': 'slow_node',
                        'title': 'Slow Node Updated',
                    }
                ],
            },
        )
        assert edited.status_code == 200
        assert edited.json()['interrupted_run'] is None

        thread.join(timeout=10)

    assert run_response['status_code'] == 200
    assert isinstance(run_response['body'], dict)
    assert run_response['body']['status'] == 'succeeded'

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert all(notice['code'] != 'run_interrupted_by_graph_edit' for notice in snapshot['notices'])


def test_run_stale_noops_when_outputs_are_already_ready(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert patch.status_code == 200

    first_run = client.post(
        f'/api/v1/projects/{project_id}/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert first_run.status_code == 200
    assert first_run.json()['status'] == 'succeeded'

    second_run = client.post(
        f'/api/v1/projects/{project_id}/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert second_run.status_code == 200
    assert second_run.json()['status'] == 'noop'


def test_run_stale_succeeds_while_edit_session_for_same_node_is_open(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)

    with TestClient(app) as client:
        opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
        project_id = opened.json()['project']['project_id']
        graph_version = opened.json()['graph']['meta']['graph_version']

        patch = client.patch(
            f'/api/v1/projects/{project_id}/graph',
            json={
                'graph_version': graph_version,
                'operations': [
                    {
                        'type': 'add_notebook_node',
                        'node_id': 'sample_node',
                        'title': 'Sample Node',
                    }
                ],
            },
        )
        assert patch.status_code == 200

        edit_run = client.post(
            f'/api/v1/projects/{project_id}/nodes/sample_node/run',
            json={'mode': 'edit_run', 'action': None},
        )
        assert edit_run.status_code == 200
        assert edit_run.json()['mode'] == 'edit_run'

        managed_run = client.post(
            f'/api/v1/projects/{project_id}/nodes/sample_node/run',
            json={'mode': 'run_stale', 'action': 'use_stale'},
        )
        assert managed_run.status_code == 200
        assert managed_run.json()['status'] == 'succeeded'

        snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
        assert all(notice['code'] != 'editor_already_open' for notice in snapshot['notices'])


def test_failed_run_records_traceback_notice_for_node(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'broken_node',
                    'title': 'Broken Node',
                }
            ],
        },
    )
    assert patch.status_code == 200

    notebook_path = project_root / 'notebooks' / 'broken_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    artifacts.push(1, name='value', data_type=int, is_output=True)
    raise RuntimeError('boom')

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/broken_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'failed'

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    notice = next(notice for notice in snapshot['notices'] if notice['code'] == 'run_failed')
    assert notice['node_id'] == 'broken_node'
    assert notice['details']['node_id'] == 'broken_node'
    assert 'Traceback' in notice['details']['traceback']


def test_run_all_is_blocked_by_pending_file_input(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'input_file',
                    'title': 'Input File',
                    'artifact_name': 'file',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'consumer',
                    'title': 'Consumer',
                },
            ],
        },
    )
    assert patch.status_code == 200

    consumer_path = project_root / 'notebooks' / 'consumer.py'
    consumer_source = """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    file_path = artifacts.pull_file(name='incoming')
    return file_path

@app.cell
def _(file_path):
    artifacts.push(len(file_path), name='path_length', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'
    consumer_path.write_text(consumer_source, encoding='utf-8')
    container.project_service.reparse_notebook_by_path(consumer_path)

    graph_version = client.get(f'/api/v1/projects/{project_id}/snapshot').json()['graph']['meta']['graph_version']
    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'input_file',
                    'source_port': 'file',
                    'target_node': 'consumer',
                    'target_port': 'incoming',
                }
            ],
        },
    )
    assert connect.status_code == 200

    run_all = client.post(
        f'/api/v1/projects/{project_id}/runs/run-all',
        json={'mode': 'run_stale'},
    )
    assert run_all.status_code == 400
    detail = run_all.json()['detail']
    assert 'Run queue is blocked by missing required inputs:' in detail
    assert '"node_id": "consumer"' in detail
    assert '"source": "input_file/file"' in detail

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert all(run['status'] not in {'queued', 'running', 'failed'} for run in snapshot['runs'])


def test_inline_constant_value_notebook_can_run_immediately(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    constant_source = """
import marimo

app = marimo.App(width='medium', app_title='Constant Value')

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    threshold_value = 42
    return threshold_value

@app.cell
def _(threshold_value):
    artifacts.push(threshold_value, name='threshold', data_type=int, is_output=True, description='Constant value output')
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'constant_block',
                    'title': 'Constant Block',
                    'source_text': constant_source,
                }
            ],
        },
    )
    assert patch.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/constant_block/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    artifact = client.get(f'/api/v1/projects/{project_id}/artifacts/constant_block/threshold')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'
    assert artifact.json()['preview']['repr'] == '42'


def test_use_stale_blocks_pending_file_inputs(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {'type': 'add_file_input_node', 'node_id': 'uploaded_file', 'title': 'Uploaded File'},
                {'type': 'add_notebook_node', 'node_id': 'consumer', 'title': 'Consumer'},
            ],
        },
    )
    assert patch.status_code == 200

    consumer_source = """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    file_path = artifacts.pull_file(name='incoming')
    return file_path

@app.cell
def _(file_path):
    artifacts.push(len(file_path), name='path_length', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'

    consumer_path = project_root / 'notebooks' / 'consumer.py'
    consumer_path.write_text(consumer_source, encoding='utf-8')
    container.project_service.reparse_notebook_by_path(consumer_path)

    connect = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': patch.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'uploaded_file',
                    'source_port': 'file',
                    'target_node': 'consumer',
                    'target_port': 'incoming',
                }
            ],
        },
    )
    assert connect.status_code == 200

    blocked = client.post(
        f'/api/v1/projects/{project_id}/nodes/consumer/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert blocked.status_code == 200
    assert blocked.json()['status'] == 'blocked'
    assert blocked.json()['blocked_inputs'][0]['source'] == 'uploaded_file/file'


def test_hidden_inputs_require_existing_defaulted_ports(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert patch.status_code == 200

    hidden_missing_default = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': patch.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_hidden_inputs',
                    'node_id': 'sample_node',
                    'hidden_inputs': ['sample_df'],
                }
            ],
        },
    )
    assert hidden_missing_default.status_code == 409


def test_edit_run_url_authenticates_marimo_session(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)

    with TestClient(app) as client:
        opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
        project_id = opened.json()['project']['project_id']
        graph_version = opened.json()['graph']['meta']['graph_version']

        patch = client.patch(
            f'/api/v1/projects/{project_id}/graph',
            json={
                'graph_version': graph_version,
                'operations': [
                    {
                        'type': 'add_notebook_node',
                        'node_id': 'sample_node',
                        'title': 'Sample Node',
                    }
                ],
            },
        )
        assert patch.status_code == 200

        run = client.post(
            f'/api/v1/projects/{project_id}/nodes/sample_node/run',
            json={'mode': 'edit_run', 'action': None},
        )
        assert run.status_code == 200

        data = run.json()
        parsed = urllib.parse.urlparse(data['url'])
        params = urllib.parse.parse_qs(parsed.query)
        assert data['mode'] == 'edit_run'
        assert params.get('access_token')

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        body = ''
        for _ in range(50):
            try:
                with opener.open(data['url'], timeout=2) as response:
                    body = response.read().decode('utf-8', errors='replace')
                    assert response.status == 200
                    break
            except urllib.error.URLError:
                time.sleep(0.2)
        else:
            raise AssertionError('Timed out waiting for Marimo edit session.')

        assert 'name="password"' not in body
        assert 'access code' not in body.lower()

        session_base = data['url'].split('?', 1)[0]
        if not session_base.endswith('/'):
            session_base = f'{session_base}/'
        status_url = urllib.parse.urljoin(session_base, 'api/status')
        with opener.open(status_url, timeout=2) as response:
            assert response.status == 200
            status = response.read().decode('utf-8', errors='replace')
        assert '"mode":"edit"' in status


def test_standalone_notebook_script_run_persists_artifacts(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)

    with TestClient(app) as client:
        opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
        project_id = opened.json()['project']['project_id']
        graph_version = opened.json()['graph']['meta']['graph_version']

        patch = client.patch(
            f'/api/v1/projects/{project_id}/graph',
            json={
                'graph_version': graph_version,
                'operations': [
                    {
                        'type': 'add_notebook_node',
                        'node_id': 'sample_node',
                        'title': 'Sample Node',
                    }
                ],
            },
        )
        assert patch.status_code == 200

        run = client.post(
            f'/api/v1/projects/{project_id}/nodes/sample_node/run',
            json={'mode': 'run_stale', 'action': 'use_stale'},
        )
        assert run.status_code == 200
        assert run.json()['status'] == 'succeeded'

        notebook_path = project_root / 'notebooks' / 'sample_node.py'
        command = [
            sys.executable,
            str(notebook_path),
        ]
        completed = subprocess.run(
            command,
            cwd=project_root,
            env={
                **os.environ,
                'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

        artifact = client.get(
            f'/api/v1/projects/{project_id}/artifacts/sample_node/sample_df'
        )
        assert artifact.status_code == 200
        assert artifact.json()['state'] == 'ready'
