from __future__ import annotations

import asyncio
from collections.abc import Container
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
from starlette.websockets import WebSocketState

from bulletjournal.api.deps import ServiceContainer
from bulletjournal.api.errors import install_error_handlers
from bulletjournal.api.routes import artifacts, checkpoints, graph, project, runs, templates
from bulletjournal.api.sse import sse_response
from bulletjournal.config import ServerConfig, bundled_web_root, normalize_base_path


def create_app(*, project_path: Path | None = None, server_config: ServerConfig | None = None) -> FastAPI:
    resolved_server_config = server_config or ServerConfig()
    base_path = normalize_base_path(resolved_server_config.base_path)
    eager_project_path = project_path.resolve() if project_path is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            app.state.container.run_service.stop()
            app.state.container.project_service.stop()

    app = FastAPI(title='BulletJournal', version='0.1.0', lifespan=lifespan)
    app.state.container = ServiceContainer()
    app.state.container.run_service.server_config = resolved_server_config
    app.state.server_config = resolved_server_config
    app.state.base_path = base_path
    if eager_project_path is not None and eager_project_path.exists():
        app.state.container.project_service.open_project(eager_project_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(resolved_server_config),
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    install_error_handlers(app)

    @app.middleware('http')
    async def forwarded_prefix_middleware(request: Request, call_next):
        forwarded_prefix = request.headers.get('x-forwarded-prefix')
        request.scope['root_path'] = normalize_base_path(forwarded_prefix)
        return await call_next(request)

    api_prefix = _route_path(base_path, '/api/v1')
    app.include_router(project.router, prefix=api_prefix)
    app.include_router(graph.router, prefix=api_prefix)
    app.include_router(artifacts.router, prefix=api_prefix)
    app.include_router(runs.router, prefix=api_prefix)
    app.include_router(checkpoints.router, prefix=api_prefix)
    app.include_router(templates.router, prefix=api_prefix)

    @app.get(_route_path(base_path, '/api/v1/events'))
    def events(request: Request, last_event_id: int | None = None):
        project_id = request.app.state.container.project_service.require_project().metadata.project_id
        return sse_response(app.state.container, project_id, request, last_event_id=last_event_id)

    @app.api_route(_route_path(base_path, '/api/v1/edit/sessions/{session_id}'), methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    @app.api_route(_route_path(base_path, '/api/v1/edit/sessions/{session_id}/{path:path}'), methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def proxy_edit_session(session_id: str, request: Request, path: str = ''):
        session = _editor_session_or_response(app, session_id)
        if not isinstance(session, dict):
            return session
        if not _editor_session_authorized(
            request.query_params.get('access_token'),
            request.headers.get('authorization', ''),
            request.cookies,
            session,
        ):
            return JSONResponse(status_code=401, content={'detail': 'Missing or invalid editor session token.'})
        target_path = _editor_target_path(session['base_url'], request.url.path, path)
        query_string = urlencode(list(request.query_params.multi_items()))
        target_url = f'http://{session["host"]}:{session["port"]}{target_path}'
        if query_string:
            target_url = f'{target_url}?{query_string}'
        body = await request.body()
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                upstream = await client.request(
                    request.method,
                    target_url,
                    content=body,
                    headers=_proxy_request_headers(request),
                )
        except httpx.ConnectError:
            return JSONResponse(status_code=503, content={'detail': 'Editor session is still starting.'})
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_proxy_response_headers(dict(upstream.headers), request=request, session=session),
            media_type=upstream.headers.get('content-type'),
        )

    @app.websocket(_route_path(base_path, '/api/v1/edit/sessions/{session_id}/ws'))
    @app.websocket(_route_path(base_path, '/api/v1/edit/sessions/{session_id}/{path:path}'))
    async def proxy_edit_session_ws(websocket: WebSocket, session_id: str, path: str = 'ws'):
        session = _editor_session_or_response(app, session_id)
        if not isinstance(session, dict):
            await websocket.close(code=4404)
            return
        if not _editor_session_authorized(
            websocket.query_params.get('access_token'),
            websocket.headers.get('authorization', ''),
            websocket.cookies,
            session,
        ):
            await websocket.close(code=4401)
            return
        target_path = _editor_target_path(session['base_url'], str(websocket.url.path), path)
        query_string = urlencode(list(websocket.query_params.multi_items()))
        target_url = f'ws://{session["host"]}:{session["port"]}{target_path}'
        if query_string:
            target_url = f'{target_url}?{query_string}'
        try:
            requested_subprotocols = [item for item in websocket.scope.get('subprotocols', []) if item]
            async with ws_connect(
                target_url,
                additional_headers=_proxy_websocket_headers(websocket),
                subprotocols=requested_subprotocols or None,
                open_timeout=30,
            ) as upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)
                await _bridge_websocket(websocket, upstream)
        except Exception:
            await _safe_close_websocket(websocket, code=1013)

    web_root = bundled_web_root()
    assets_dir = web_root / 'assets'
    if assets_dir.exists():
        app.mount(_route_path(base_path, '/assets'), StaticFiles(directory=assets_dir), name='assets')

    @app.get('/healthz')
    @app.get(_route_path(base_path, '/healthz'))
    def healthz():
        return {'status': 'ok'}

    @app.get(_route_path(base_path, '/{path:path}'))
    def spa(path: str):
        if resolved_server_config.dev_frontend_url:
            suffix = path.lstrip('/')
            if suffix:
                return RedirectResponse(f"{resolved_server_config.dev_frontend_url.rstrip('/')}/{suffix}")
            return RedirectResponse(str(resolved_server_config.dev_frontend_url))
        candidate = web_root / path
        if path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        index = web_root / 'index.html'
        if index.exists():
            html = index.read_text(encoding='utf-8')
            snippet = f"<script>window.__BULLETJOURNAL_BASE_PATH__ = {base_path!r};</script>"
            injected = html.replace('</head>', f'{snippet}</head>', 1) if '</head>' in html else f'{html}{snippet}'
            return HTMLResponse(injected)
        return JSONResponse(
            status_code=503,
            content={'detail': 'Frontend assets are not built yet. Use API endpoints directly or build the web app.'},
        )

    return app


def _cors_allowed_origins(server_config: ServerConfig) -> list[str]:
    origins = {
        f'http://127.0.0.1:{server_config.port}',
        f'http://localhost:{server_config.port}',
    }
    if server_config.dev_frontend_url:
        parsed = urlsplit(server_config.dev_frontend_url)
        if parsed.scheme and parsed.netloc:
            origins.add(f'{parsed.scheme}://{parsed.netloc}')
    return sorted(origins)


def _route_path(base_path: str, suffix: str) -> str:
    normalized_suffix = suffix if suffix.startswith('/') else f'/{suffix}'
    return f'{base_path}{normalized_suffix}' if base_path else normalized_suffix


def _proxy_request_headers(request: Request) -> dict[str, str]:
    excluded = {'host', 'content-length'}
    return {key: value for key, value in request.headers.items() if key.lower() not in excluded}


def _proxy_response_headers(
    headers: dict[str, str],
    *,
    request: Request | None = None,
    session: dict[str, object] | None = None,
) -> dict[str, str]:
    excluded = {'content-length', 'transfer-encoding', 'connection', 'content-encoding'}
    resolved = {key: value for key, value in headers.items() if key.lower() not in excluded}
    if request is not None and session is not None:
        location = resolved.get('location') or resolved.get('Location')
        if location:
            rewritten = _rewrite_upstream_location(location, request=request, session=session)
            if 'location' in resolved:
                resolved['location'] = rewritten
            if 'Location' in resolved:
                resolved['Location'] = rewritten
    return resolved


def _editor_session_or_response(app: FastAPI, session_id: str) -> dict[str, object] | JSONResponse:
    session = app.state.container.run_service.session_manager.get(session_id)
    if session is None:
        return JSONResponse(status_code=404, content={'detail': f'Unknown editor session `{session_id}`.'})
    return {
        'host': session.host,
        'port': session.port,
        'token': session.token,
        'base_url': session.base_url,
    }


def _editor_session_authorized(
    token: str | None,
    authorization: str,
    cookies: dict[str, str] | object,
    session: dict[str, object],
) -> bool:
    resolved_token = str(session['token'])
    if token == resolved_token or authorization == f'Bearer {resolved_token}':
        return True
    cookie_name = f"session_{session['port']}"
    if isinstance(cookies, dict):
        return cookie_name in cookies
    if isinstance(cookies, dict):
        return cookie_name in cookies
    if isinstance(cookies, Container):
        try:
            container = cookies
            return bool(container.__contains__(cookie_name))
        except TypeError:
            return False
    return False


def _editor_target_path(base_url: object, request_path: str, path: str) -> str:
    resolved_base_url = str(base_url)
    has_trailing_slash = request_path.endswith('/')
    if path:
        suffix = f'/{path}'
    elif has_trailing_slash:
        suffix = '/'
    else:
        suffix = ''
    return f'{resolved_base_url}{suffix}'


def _rewrite_upstream_location(location: str, *, request: Request, session: dict[str, object]) -> str:
    session_host = str(session['host'])
    session_port = str(session['port'])
    session_base_url = str(session['base_url']).rstrip('/')
    public_base = str(request.base_url).rstrip('/')
    public_session_base = f'{public_base}{session_base_url}'

    for prefix in (
        f'http://{session_host}:{session_port}{session_base_url}',
        f'ws://{session_host}:{session_port}{session_base_url}',
    ):
        if location.startswith(prefix):
            return f'{public_session_base}{location[len(prefix):]}'

    if location == session_base_url or location.startswith(f'{session_base_url}/'):
        return f'{public_base}{location}'

    return location


def _proxy_websocket_headers(websocket: WebSocket) -> list[tuple[str, str]]:
    excluded = {'host', 'connection', 'upgrade', 'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-extensions', 'sec-websocket-protocol'}
    return [(key, value) for key, value in websocket.headers.items() if key.lower() not in excluded]


async def _bridge_websocket(websocket: WebSocket, upstream) -> None:
    async def client_to_upstream() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message['type'] == 'websocket.disconnect':
                    break
                if message.get('text') is not None:
                    await upstream.send(message['text'])
                elif message.get('bytes') is not None:
                    await upstream.send(message['bytes'])
        except WebSocketDisconnect:
            pass
        finally:
            try:
                await upstream.close()
            except ConnectionClosed:
                pass

    async def upstream_to_client() -> None:
        try:
            while True:
                message = await upstream.recv()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
        except ConnectionClosed:
            pass
        finally:
            await _safe_close_websocket(websocket)

    client_task = asyncio.create_task(client_to_upstream())
    upstream_task = asyncio.create_task(upstream_to_client())
    done, pending = await asyncio.wait({client_task, upstream_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        await task


async def _safe_close_websocket(websocket: WebSocket, code: int = 1000) -> None:
    if websocket.client_state is WebSocketState.DISCONNECTED:
        return
    if websocket.application_state is WebSocketState.DISCONNECTED:
        return
    try:
        await websocket.close(code=code)
    except RuntimeError:
        return
