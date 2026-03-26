from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.sync.server import serve

import bulletjournal.api.app as api_app_module
import bulletjournal.api.sse as sse_module
from bulletjournal.config import ServerConfig
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.storage.project_fs import init_project_root, require_project_root
from bulletjournal.storage.state_db import StateDB


class FakeRequest:
    def __init__(self, disconnect_states: list[bool], headers: dict[str, str] | None = None) -> None:
        self._disconnect_states = iter(disconnect_states)
        self.headers = headers or {}

    async def is_disconnected(self) -> bool:
        return next(self._disconnect_states, True)


async def _collect_chunks(response) -> list[str]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk)
    return chunks


class FakeProjectService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.opened: list[Path] = []
        self.stop_calls = 0
        self._project = None

    def open_project(self, path: Path) -> None:
        self.opened.append(path)
        if self.error is not None:
            raise self.error
        try:
            resolved = require_project_root(path)
        except ProjectValidationError:
            self._project = SimpleNamespace(
                paths=SimpleNamespace(root=path), metadata=SimpleNamespace(project_id='test-project')
            )
            return
        self._project = SimpleNamespace(
            paths=resolved,
            metadata=SimpleNamespace(project_id='test-project'),
            state_db=StateDB(resolved.state_db_path),
        )

    def require_project(self):
        if self._project is None:
            raise RuntimeError('No project open')
        return self._project

    def stop(self) -> None:
        self.stop_calls += 1


class FakeRunService:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.session_manager = SimpleNamespace(get=lambda session_id: None)

    def stop(self) -> None:
        self.stop_calls += 1


class FakeContainer:
    def __init__(self, *, project_error: Exception | None = None, event_service=None) -> None:
        self.project_service = FakeProjectService(project_error)
        self.run_service = FakeRunService()
        self.event_service = event_service or SimpleNamespace(
            events_after=lambda last_event_id: {'events': [], 'reset_required': False, 'earliest_available_id': 0}
        )


def _make_app(
    monkeypatch,
    web_root: Path,
    *,
    project_path: Path | None = None,
    server_config: ServerConfig | None = None,
    container=None,
):
    resolved_container = container or FakeContainer()
    monkeypatch.setattr(api_app_module, 'ServiceContainer', lambda: resolved_container)
    monkeypatch.setattr(api_app_module, 'bundled_web_root', lambda: web_root)
    app = api_app_module.create_app(project_path=project_path, server_config=server_config)
    return app, resolved_container


def test_create_app_opens_project_on_startup_and_stops_services(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    project_root = tmp_path / 'project'
    project_root.mkdir()
    app, container = _make_app(monkeypatch, web_root, project_path=project_root)

    with TestClient(app):
        pass

    assert container.project_service.opened == [project_root]
    assert container.run_service.stop_calls == 1
    assert container.project_service.stop_calls == 1


def test_create_app_fails_startup_when_project_open_fails(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    project_root = tmp_path / 'project'
    project_root.mkdir()
    container = FakeContainer(project_error=FileNotFoundError('gone'))
    try:
        _make_app(monkeypatch, web_root, project_path=project_root, container=container)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('expected app creation to fail')

    assert container.project_service.opened == [project_root]


def test_create_app_serves_assets_files_index_and_503(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    assets_dir = web_root / 'assets'
    assets_dir.mkdir(parents=True)
    (assets_dir / 'app.js').write_text('console.log(1);', encoding='utf-8')
    (web_root / 'guide.html').write_text('guide', encoding='utf-8')
    (web_root / 'index.html').write_text('index', encoding='utf-8')

    app, _ = _make_app(monkeypatch, web_root)

    with TestClient(app) as client:
        asset = client.get('/assets/app.js')
        file_response = client.get('/guide.html')
        fallback = client.get('/missing-route')

    assert asset.status_code == 200
    assert asset.text == 'console.log(1);'
    assert file_response.text == 'guide'
    assert 'index' in fallback.text
    assert '__BULLETJOURNAL_BASE_PATH__' in fallback.text

    empty_root = tmp_path / 'empty-web'
    empty_root.mkdir()
    app, _ = _make_app(monkeypatch, empty_root)
    with TestClient(app) as client:
        unavailable = client.get('/missing-route')

    assert unavailable.status_code == 503
    assert unavailable.json()['detail'].startswith('Frontend assets are not built yet')


def test_create_app_downloads_execution_logs(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    project_root = init_project_root(tmp_path / 'project').root
    app, _ = _make_app(monkeypatch, web_root, project_path=project_root)

    with TestClient(app) as client:
        project_paths = require_project_root(project_root)
        stdout_log = project_paths.execution_logs_dir / 'run-1_node_a.stdout.log'
        stdout_log.write_text('hello stdout\n', encoding='utf-8')
        state_db = StateDB(project_paths.state_db_path)
        state_db.upsert_orchestrator_execution_meta(
            node_id='node_a',
            run_id='run-1',
            status='succeeded',
            started_at='2026-03-26T00:00:00Z',
            stdout_path=str(stdout_log),
        )
        response = client.get('/api/v1/nodes/node_a/execution-logs/stdout/download')

    assert response.status_code == 200
    assert response.text == 'hello stdout\n'
    assert 'run-1_node_a.stdout.log' in response.headers['content-disposition']


def test_create_app_redirects_to_dev_frontend(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    app, _ = _make_app(
        monkeypatch,
        web_root,
        server_config=ServerConfig(dev_frontend_url='http://127.0.0.1:5173/app/'),
    )

    with TestClient(app) as client:
        root = client.get('/', follow_redirects=False)
        nested = client.get('/nested/page', follow_redirects=False)

    assert root.headers['location'] == 'http://127.0.0.1:5173/app/'
    assert nested.headers['location'] == 'http://127.0.0.1:5173/app/nested/page'


def test_edit_session_proxy_rewrites_upstream_redirect_location(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()

    class FakeProcess:
        def poll(self):
            return None

    session = SimpleNamespace(
        host='127.0.0.1',
        port=52012,
        base_url='/api/v1/edit/sessions/demo',
        process=FakeProcess(),
    )
    container = FakeContainer()
    container.run_service.session_manager = SimpleNamespace(
        get=lambda session_id: session if session_id == 'demo' else None
    )

    class FakeResponse:
        def __init__(self):
            self.content = b''
            self.status_code = 307
            self.headers = {
                'location': 'http://127.0.0.1:52012/api/v1/edit/sessions/demo/',
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(api_app_module.httpx, 'AsyncClient', FakeClient)
    app, _ = _make_app(monkeypatch, web_root, container=container)

    with TestClient(app) as client:
        response = client.get('/api/v1/edit/sessions/demo', follow_redirects=False)

    assert response.status_code == 307
    assert response.headers['location'] == 'http://testserver/api/v1/edit/sessions/demo/'


def test_edit_session_proxy_accepts_upstream_session_cookie(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()

    class FakeProcess:
        def poll(self):
            return None

    session = SimpleNamespace(
        host='127.0.0.1',
        port=52012,
        base_url='/api/v1/edit/sessions/demo',
        process=FakeProcess(),
    )
    container = FakeContainer()
    container.run_service.session_manager = SimpleNamespace(
        get=lambda session_id: session if session_id == 'demo' else None
    )

    class FakeResponse:
        def __init__(self):
            self.content = b'ok'
            self.status_code = 200
            self.headers = {'content-type': 'text/plain'}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(api_app_module.httpx, 'AsyncClient', FakeClient)
    app, _ = _make_app(monkeypatch, web_root, container=container)

    with TestClient(app) as client:
        client.cookies.set('session_52012', 'cookie-value')
        response = client.get('/api/v1/edit/sessions/demo/assets/useNonce.js')

    assert response.status_code == 200
    assert response.text == 'ok'


def test_create_app_serves_assets_and_api_under_base_path(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    assets_dir = web_root / 'assets'
    assets_dir.mkdir(parents=True)
    (assets_dir / 'app.js').write_text('console.log(1);', encoding='utf-8')
    (web_root / 'index.html').write_text('index', encoding='utf-8')

    app, _ = _make_app(monkeypatch, web_root, server_config=ServerConfig(base_path='/p/demo'))

    with TestClient(app) as client:
        asset = client.get('/p/demo/assets/app.js')
        health = client.get('/p/demo/healthz')
        root_health = client.get('/healthz')
        fallback = client.get('/p/demo/missing-route')

    assert asset.status_code == 200
    assert health.json() == {'status': 'ok'}
    assert root_health.json() == {'status': 'ok'}
    assert "'/p/demo'" in fallback.text


def test_create_app_proxies_editor_websocket(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = int(listener.getsockname()[1])
    listener.close()

    def echo(connection) -> None:
        message = connection.recv()
        connection.send(f'echo:{message}')

    server = serve(echo, '127.0.0.1', port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)

    class FakeProcess:
        def poll(self):
            return None

    session = SimpleNamespace(
        host='127.0.0.1',
        port=port,
        base_url='/api/v1/edit/sessions/demo',
        process=FakeProcess(),
    )
    container = FakeContainer()
    container.run_service.session_manager = SimpleNamespace(
        get=lambda session_id: session if session_id == 'demo' else None
    )

    app, _ = _make_app(monkeypatch, web_root, container=container)

    try:
        with TestClient(app) as client, client.websocket_connect('/api/v1/edit/sessions/demo/ws') as websocket:
            websocket.send_text('hello')
            assert websocket.receive_text() == 'echo:hello'
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_create_app_preserves_upstream_websocket_close_reason(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = int(listener.getsockname()[1])
    listener.close()

    def close_immediately(connection) -> None:
        connection.close(code=1000, reason='MARIMO_NO_SESSION_ID')

    server = serve(close_immediately, '127.0.0.1', port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)

    class FakeProcess:
        def poll(self):
            return None

    session = SimpleNamespace(
        host='127.0.0.1',
        port=port,
        base_url='/api/v1/edit/sessions/demo',
        process=FakeProcess(),
    )
    container = FakeContainer()
    container.run_service.session_manager = SimpleNamespace(
        get=lambda session_id: session if session_id == 'demo' else None
    )

    app, _ = _make_app(monkeypatch, web_root, container=container)

    try:
        with (
            TestClient(app) as client,
            client.websocket_connect('/api/v1/edit/sessions/demo/ws?session_id=s_demo') as websocket,
        ):
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert excinfo.value.code == 1000
    assert excinfo.value.reason == 'MARIMO_NO_SESSION_ID'


def test_cors_allowed_origins_include_valid_dev_frontend_only() -> None:
    allowed = api_app_module._cors_allowed_origins(
        ServerConfig(port=9000, dev_frontend_url='https://frontend.example:5173/path')
    )
    invalid = api_app_module._cors_allowed_origins(ServerConfig(port=9000, dev_frontend_url='frontend-example'))

    assert 'http://127.0.0.1:9000' in allowed
    assert 'http://localhost:9000' in allowed
    assert 'https://frontend.example:5173' in allowed
    assert invalid == ['http://127.0.0.1:9000', 'http://localhost:9000']


def test_sse_response_emits_reset_event_from_header_cursor(monkeypatch) -> None:
    calls: list[int] = []

    class FakeEventService:
        def events_after(self, last_event_id: int):
            calls.append(last_event_id)
            return {'events': [], 'reset_required': True, 'earliest_available_id': 12}

    request: Any = FakeRequest([False, True], headers={'last-event-id': '9'})
    response = sse_module.sse_response(
        SimpleNamespace(event_service=FakeEventService()), 'demo', request, last_event_id=1
    )

    chunks = asyncio.run(_collect_chunks(response))

    assert calls == [9]
    assert chunks[0] == 'retry: 1000\n\n'
    assert 'event: stream.reset' in chunks[1]
    assert '"earliest_available_id": 12' in chunks[1]


def test_sse_response_filters_projects_and_emits_keepalive(monkeypatch) -> None:
    calls: list[int] = []
    responses = [
        {
            'reset_required': False,
            'earliest_available_id': 1,
            'events': [
                {'id': 4, 'event_type': 'skip.me', 'project_id': 'other', 'graph_version': 1, 'payload': {}},
                {
                    'id': 5,
                    'event_type': 'graph.updated',
                    'project_id': 'demo',
                    'graph_version': 2,
                    'payload': {'ok': True},
                },
            ],
        },
        {'reset_required': False, 'earliest_available_id': 1, 'events': []},
    ]

    class FakeEventService:
        def events_after(self, last_event_id: int):
            calls.append(last_event_id)
            return responses.pop(0)

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(sse_module.asyncio, 'sleep', fake_sleep)
    request: Any = FakeRequest([False, False, True])
    response = sse_module.sse_response(
        SimpleNamespace(event_service=FakeEventService()), 'demo', request, last_event_id=0
    )

    chunks = asyncio.run(_collect_chunks(response))

    assert calls == [0, 5]
    assert chunks[0] == 'retry: 1000\n\n'
    assert 'event: graph.updated' in chunks[1]
    assert '"project_id": "demo"' in chunks[1]
    assert chunks[2] == ': keepalive\n\n'


def test_resolve_last_event_id_prefers_header_and_defaults_query() -> None:
    assert sse_module._resolve_last_event_id(' 7 ', 3) == 7
    assert sse_module._resolve_last_event_id(None, 4) == 4
    assert sse_module._resolve_last_event_id(None, None) == 0
