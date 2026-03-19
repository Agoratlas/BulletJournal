from __future__ import annotations

from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.config import ServerConfig
from bulletjournal.storage.project_fs import init_project_root


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
