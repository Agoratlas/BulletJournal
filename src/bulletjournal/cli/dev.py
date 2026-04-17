from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bulletjournal.cli.start import start_server


def dev_server(path: str | None, *, open_browser: bool = False, base_path: str = '') -> None:
    vite_command = None
    if shutil.which('pnpm'):
        vite_command = ['pnpm', 'dev', '--host', '127.0.0.1', '--port', '5173']
    elif shutil.which('corepack'):
        vite_command = ['corepack', 'pnpm', 'dev', '--host', '127.0.0.1', '--port', '5173']

    vite_process = None
    web_root = Path(__file__).resolve().parents[3] / 'web'
    try:
        if vite_command is not None:
            vite_process = subprocess.Popen(vite_command, cwd=web_root)  # noqa: S603
        start_server(
            path,
            open_browser=open_browser,
            base_path=base_path,
            reload=True,
            dev_frontend_url='http://127.0.0.1:5173',
        )
    finally:
        if vite_process is not None and vite_process.poll() is None:
            vite_process.terminate()
