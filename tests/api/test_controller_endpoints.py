from __future__ import annotations

import time

from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.config import ServerConfig
from bulletjournal.domain.enums import ArtifactState
from bulletjournal.storage.project_fs import init_project_root


def _wait_for_run_status(client: TestClient, run_id: str, expected: str, *, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get('/api/v1/project/snapshot').json()
        run = next((entry for entry in snapshot['runs'] if entry['run_id'] == run_id), None)
        if run is not None and run['status'] == expected:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f'Run {run_id} did not reach {expected}.')


def test_controller_endpoints_require_bearer_token(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    app = create_app(project_path=project_root, server_config=ServerConfig(controller_token='secret-token'))
    client = TestClient(app)

    unauthorized = client.get('/api/v1/controller/status')
    authorized = client.get('/api/v1/controller/status', headers={'Authorization': 'Bearer secret-token'})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()['project_id'] == 'study-a'


def test_controller_can_mark_environment_changed(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    app = create_app(project_path=project_root, server_config=ServerConfig(controller_token='secret-token'))
    client = TestClient(app)

    response = client.post(
        '/api/v1/controller/mark-environment-changed',
        headers={'Authorization': 'Bearer secret-token'},
        json={'reason': 'requirements updated by controller', 'mark_all_artifacts_stale': True},
    )

    assert response.status_code == 200
    assert response.json()['reason'] == 'requirements updated by controller'
    assert response.json()['mark_all_artifacts_stale'] is True


def test_controller_environment_change_preserves_frozen_outputs_and_notifies(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    app = create_app(project_path=project_root, server_config=ServerConfig(controller_token='secret-token'))
    client = TestClient(app)
    container = app.state.container

    snapshot = client.get('/api/v1/project/snapshot').json()
    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': snapshot['graph']['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200
    run = client.post('/api/v1/nodes/sample_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200

    _wait_for_run_status(client, run.json()['run_id'], 'succeeded')
    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['graph']['meta']['graph_version'],
            'operations': [{'type': 'update_node_frozen', 'node_id': 'sample_node', 'frozen': True}],
        },
    )
    assert frozen.status_code == 200

    response = client.post(
        '/api/v1/controller/mark-environment-changed',
        headers={'Authorization': 'Bearer secret-token'},
        json={'reason': 'requirements updated by controller', 'mark_all_artifacts_stale': True},
    )

    assert response.status_code == 200
    assert response.json()['stale_count'] == 0
    assert response.json()['frozen_notice']['code'] == 'environment_changed_frozen_blocks'
    assert (
        container.project_service.require_project().state_db.get_artifact_head('sample_node', 'sample_df')['state']
        == ArtifactState.READY.value
    )


def test_environment_change_reactivates_previously_dismissed_notice(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    app = create_app(project_path=project_root, server_config=ServerConfig(controller_token='secret-token'))
    client = TestClient(app)

    first = client.post(
        '/api/v1/controller/mark-environment-changed',
        headers={'Authorization': 'Bearer secret-token'},
        json={'reason': 'first change', 'mark_all_artifacts_stale': True},
    )
    assert first.status_code == 200

    dismissed = client.post(f'/api/v1/notices/{first.json()["notice"]["issue_id"]}/dismiss')
    assert dismissed.status_code == 200

    repeated = client.post(
        '/api/v1/controller/mark-environment-changed',
        headers={'Authorization': 'Bearer secret-token'},
        json={'reason': 'second change', 'mark_all_artifacts_stale': True},
    )
    assert repeated.status_code == 200

    notices = client.get('/api/v1/project/snapshot').json()['notices']
    notice = next(notice for notice in notices if notice['issue_id'] == repeated.json()['notice']['issue_id'])
    assert notice['details']['reason'] == 'second change'
