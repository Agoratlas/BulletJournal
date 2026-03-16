from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn

from bulletjournal.config import DEFAULT_HOST, DEFAULT_PORT, ServerConfig


def start_server(
    path: str | None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    reload: bool = False,
    dev_frontend_url: str | None = None,
) -> None:
    project_path = Path(path or '.').resolve()
    from bulletjournal.api.app import create_app

    app = create_app(
        project_path=project_path,
        server_config=ServerConfig(
            host=host,
            port=port,
            open_browser=open_browser,
            reload=reload,
            dev_frontend_url=dev_frontend_url,
        ),
    )
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f'http://{host}:{port}')).start()
    uvicorn.run(app, host=host, port=port, reload=reload)
