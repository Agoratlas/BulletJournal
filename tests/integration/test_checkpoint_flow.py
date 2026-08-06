import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.domain.models import CheckpointRecord
from bulletjournal.storage.project_fs import init_project_root


def wait_for_run_status(client: TestClient, run_id: str, expected: str, *, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get('/api/v1/project/snapshot').json()
        run = next((entry for entry in snapshot['runs'] if entry['run_id'] == run_id), None)
        if run is not None and run['status'] == expected:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f'Run `{run_id}` did not reach status `{expected}` within {timeout} seconds.')


def test_checkpoint_restore_recovers_graph_state(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [{'type': 'add_notebook_node', 'node_id': 'checkpointed', 'title': 'Original Title'}],
        },
    )
    assert patch.status_code == 200

    checkpoint = client.post('/api/v1/checkpoints')
    assert checkpoint.status_code == 200
    checkpoint_id = checkpoint.json()['checkpoint_id']

    graph_version = client.get('/api/v1/graph').json()['meta']['graph_version']
    retitle = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [{'type': 'update_node_title', 'node_id': 'checkpointed', 'title': 'Updated Title'}],
        },
    )
    assert retitle.status_code == 200

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'checkpointed')
    assert node['title'] == 'Original Title'


def test_checkpoint_restore_removes_post_checkpoint_nodes_and_artifacts(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    base = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'baseline',
                    'title': 'Baseline',
                }
            ],
        },
    )
    assert base.status_code == 200

    checkpoint = client.post('/api/v1/checkpoints')
    assert checkpoint.status_code == 200
    checkpoint_id = checkpoint.json()['checkpoint_id']

    added = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': base.json()['graph']['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'after_checkpoint',
                    'title': 'After Checkpoint',
                    'template_ref': 'builtin/value_input',
                }
            ],
        },
    )
    assert added.status_code == 200

    run = client.post(
        '/api/v1/nodes/after_checkpoint/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'running'
    wait_for_run_status(client, run.json()['run_id'], 'succeeded')

    artifact = client.get('/api/v1/artifacts/after_checkpoint/value')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    assert {node['id'] for node in snapshot['graph']['nodes']} == {'baseline'}
    assert all(artifact['node_id'] != 'after_checkpoint' for artifact in snapshot['artifacts'])
    assert not (project_root / 'notebooks' / 'after_checkpoint.py').exists()

    missing = client.get('/api/v1/artifacts/after_checkpoint/value')
    assert missing.status_code == 404


def test_checkpoint_restore_marks_restored_outputs_stale(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patch = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                    'x': 420,
                    'y': 80,
                },
            ],
        },
    )
    assert patch.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': patch.json()['graph']['meta']['graph_version'],
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
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'running'
    wait_for_run_status(client, run.json()['run_id'], 'succeeded')

    ready = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert ready.status_code == 200
    assert ready.json()['state'] == 'ready'
    assert ready.json()['preview']['rows'] == 42

    checkpoint = client.post('/api/v1/checkpoints')
    assert checkpoint.status_code == 200
    checkpoint_id = checkpoint.json()['checkpoint_id']

    disconnected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['graph']['meta']['graph_version'],
            'operations': [
                {
                    'type': 'remove_edge',
                    'edge_id': 'value_source.value__table_sink.sample_count',
                }
            ],
        },
    )
    assert disconnected.status_code == 200

    rerun = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert rerun.status_code == 200
    assert rerun.json()['status'] == 'running'
    wait_for_run_status(client, rerun.json()['run_id'], 'succeeded')

    defaulted = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert defaulted.status_code == 200
    assert defaulted.json()['state'] == 'ready'
    assert defaulted.json()['preview']['rows'] == 10

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    restored_artifact = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert restored_artifact.status_code == 200
    assert restored_artifact.json()['state'] == 'ready'
    assert restored_artifact.json()['preview']['rows'] == 42


def test_checkpoint_restore_marks_constant_and_dependent_output_ready(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    graph_version = client.get('/api/v1/project/snapshot').json()['graph']['meta']['graph_version']
    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'sample_count',
                    'title': 'Sample Count',
                    'data_type': 'int',
                    'value': 42,
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['graph']['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'sample_count',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    wait_for_run_status(client, run.json()['run_id'], 'succeeded')

    checkpoint = client.post('/api/v1/checkpoints')
    assert checkpoint.status_code == 200
    checkpoint_id = checkpoint.json()['checkpoint_id']

    updated = client.post('/api/v1/constants/sample_count/value', json={'value': 10})
    assert updated.status_code == 200

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    constant = client.get('/api/v1/artifacts/sample_count/value')
    assert constant.status_code == 200
    assert constant.json()['state'] == 'ready'
    assert constant.json()['preview']['repr'] == '42'

    output = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert output.status_code == 200
    assert output.json()['state'] == 'ready'
    assert output.json()['preview']['rows'] == 42


def test_manual_checkpoint_endpoint_still_creates_checkpoint_even_when_auto_checkpoint_not_due(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    recent_created_at = (
        (datetime.now(tz=UTC) - timedelta(minutes=9)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    )
    monkeypatch.setattr(
        app.state.container.project_service.require_project().state_db,
        'list_checkpoints',
        lambda: [
            CheckpointRecord(
                checkpoint_id='recent', created_at=recent_created_at, graph_version=1, path='x', restored_at=None
            )
        ],
    )

    created: list[str] = []
    original_create_checkpoint = app.state.container.checkpoint_service.create_checkpoint

    def wrapped_create_checkpoint() -> dict[str, object]:
        created.append('created')
        return original_create_checkpoint()

    monkeypatch.setattr(app.state.container.checkpoint_service, 'create_checkpoint', wrapped_create_checkpoint)

    response = client.post('/api/v1/checkpoints')

    assert response.status_code == 200
    assert created == ['created']


def test_automatic_checkpoint_for_pipeline_template_includes_materialized_notebooks(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot').json()
    graph_version = opened['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_movie_pipeline',
                    'x': 200,
                    'y': 240,
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    assert len(snapshot['checkpoints']) == 1
    checkpoint_id = snapshot['checkpoints'][0]['checkpoint_id']

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    reopened = create_app(project_path=project_root)
    reopened_client = TestClient(reopened)
    reopened_snapshot = reopened_client.get('/api/v1/project/snapshot').json()

    notebook_ids = {
        'advanced_rating_analysis',
        'duration_and_date_analysis',
        'movie_dataset_download',
        'movie_genre_analysis',
        'movie_recommendation',
    }
    restored_node_ids = {node['id'] for node in reopened_snapshot['graph']['nodes']}
    assert notebook_ids <= restored_node_ids
    for node_id in notebook_ids:
        assert (project_root / 'notebooks' / f'{node_id}.py').is_file()
