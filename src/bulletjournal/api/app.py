from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from bulletjournal.api.deps import ServiceContainer
from bulletjournal.api.errors import install_error_handlers
from bulletjournal.api.routes import artifacts, checkpoints, graph, project, runs, templates
from bulletjournal.config import bundled_web_root, ServerConfig
from bulletjournal.api.sse import sse_response


def create_app(*, project_path: Path | None = None, server_config: ServerConfig | None = None) -> FastAPI:
    resolved_server_config = server_config or ServerConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if project_path is not None and project_path.exists():
            try:
                app.state.container.project_service.open_project(project_path)
            except FileNotFoundError:
                pass
        try:
            yield
        finally:
            app.state.container.run_service.stop()
            app.state.container.project_service.stop()

    app = FastAPI(title='BulletJournal', version='0.1.0', lifespan=lifespan)
    app.state.container = ServiceContainer()
    app.state.server_config = resolved_server_config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(resolved_server_config),
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    install_error_handlers(app)

    app.include_router(project.router, prefix='/api/v1')
    app.include_router(graph.router, prefix='/api/v1')
    app.include_router(artifacts.router, prefix='/api/v1')
    app.include_router(runs.router, prefix='/api/v1')
    app.include_router(checkpoints.router, prefix='/api/v1')
    app.include_router(templates.router, prefix='/api/v1')

    @app.get('/api/v1/projects/{project_id}/events')
    def events(project_id: str, request: Request, last_event_id: int | None = None):
        return sse_response(app.state.container, project_id, request, last_event_id=last_event_id)

    web_root = bundled_web_root()
    assets_dir = web_root / 'assets'
    if assets_dir.exists():
        app.mount('/assets', StaticFiles(directory=assets_dir), name='assets')

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok'}

    @app.get('/{path:path}')
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
            return FileResponse(index)
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
