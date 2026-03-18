from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import bulletjournal.api.app as api_app_module
import bulletjournal.api.sse as sse_module
from bulletjournal.config import ServerConfig


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

    def open_project(self, path: Path) -> None:
        self.opened.append(path)
        if self.error is not None:
            raise self.error

    def stop(self) -> None:
        self.stop_calls += 1


class FakeRunService:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class FakeContainer:
    def __init__(self, *, project_error: Exception | None = None, event_service=None) -> None:
        self.project_service = FakeProjectService(project_error)
        self.run_service = FakeRunService()
        self.event_service = event_service or SimpleNamespace(events_after=lambda last_event_id: {'events': [], 'reset_required': False, 'earliest_available_id': 0})


def _make_app(monkeypatch, web_root: Path, *, project_path: Path | None = None, server_config: ServerConfig | None = None, container=None):
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


def test_create_app_ignores_missing_project_file_on_startup(monkeypatch, tmp_path: Path) -> None:
    web_root = tmp_path / 'web'
    web_root.mkdir()
    project_root = tmp_path / 'project'
    project_root.mkdir()
    container = FakeContainer(project_error=FileNotFoundError('gone'))
    app, container = _make_app(monkeypatch, web_root, project_path=project_root, container=container)

    with TestClient(app):
        pass

    assert container.project_service.opened == [project_root]
    assert container.run_service.stop_calls == 1
    assert container.project_service.stop_calls == 1


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
    assert fallback.text == 'index'

    empty_root = tmp_path / 'empty-web'
    empty_root.mkdir()
    app, _ = _make_app(monkeypatch, empty_root)
    with TestClient(app) as client:
        unavailable = client.get('/missing-route')

    assert unavailable.status_code == 503
    assert unavailable.json()['detail'].startswith('Frontend assets are not built yet')


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


def test_cors_allowed_origins_include_valid_dev_frontend_only() -> None:
    allowed = api_app_module._cors_allowed_origins(ServerConfig(port=9000, dev_frontend_url='https://frontend.example:5173/path'))
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

    request = FakeRequest([False, True], headers={'last-event-id': '9'})
    response = sse_module.sse_response(SimpleNamespace(event_service=FakeEventService()), 'demo', request, last_event_id=1)

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
                {'id': 5, 'event_type': 'graph.updated', 'project_id': 'demo', 'graph_version': 2, 'payload': {'ok': True}},
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
    request = FakeRequest([False, False, True])
    response = sse_module.sse_response(SimpleNamespace(event_service=FakeEventService()), 'demo', request, last_event_id=0)

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
