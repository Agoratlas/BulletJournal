from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


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
            'graph_version': base.json()['meta']['graph_version'],
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
    assert run.json()['status'] == 'succeeded'

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
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

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
            'graph_version': connected.json()['meta']['graph_version'],
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
    assert rerun.json()['status'] == 'succeeded'

    defaulted = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert defaulted.status_code == 200
    assert defaulted.json()['state'] == 'ready'
    assert defaulted.json()['preview']['rows'] == 10

    restored = client.post(f'/api/v1/checkpoints/{checkpoint_id}/restore')
    assert restored.status_code == 200

    restored_artifact = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert restored_artifact.status_code == 200
    assert restored_artifact.json()['state'] == 'stale'
    assert restored_artifact.json()['preview']['rows'] == 10
