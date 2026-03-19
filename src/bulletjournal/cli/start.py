from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn

from bulletjournal.config import DEFAULT_HOST, DEFAULT_PORT, ServerConfig, controller_token_from_env, normalize_base_path
from bulletjournal.storage import require_project_root


def start_server(
    path: str | None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    base_path: str = '',
    open_browser: bool = False,
    reload: bool = False,
    dev_frontend_url: str | None = None,
) -> None:
    project_path = require_project_root(Path(path or '.').resolve()).root
    from bulletjournal.api.app import create_app

    normalized_base_path = normalize_base_path(base_path)
    app = create_app(
        project_path=project_path,
        server_config=ServerConfig(
            host=host,
            port=port,
            base_path=normalized_base_path,
            open_browser=open_browser,
            reload=reload,
            dev_frontend_url=dev_frontend_url,
            controller_token=controller_token_from_env(),
        ),
    )
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f'http://{host}:{port}{normalized_base_path or "/"}')).start()
    uvicorn.run(app, host=host, port=port, reload=reload)
