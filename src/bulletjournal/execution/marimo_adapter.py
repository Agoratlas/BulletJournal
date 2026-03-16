from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType


def load_notebook_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f'bulletjournal_notebook_{path.stem}', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load notebook module from {path}.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute_notebook(path: Path) -> dict[str, object]:
    module = load_notebook_module(path)
    app = getattr(module, 'app', None)
    if app is None:
        raise RuntimeError(f'Notebook {path} does not define `app`.')
    result = app.run()
    return {'result': result}


def launch_editor(
    path: Path,
    *,
    host: str,
    port: int,
    base_url: str,
    token: str,
    environment: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    return subprocess.Popen(
        [
            sys.executable,
            '-m',
            'marimo',
            'edit',
            str(path),
            '--headless',
            '--host',
            host,
            '--port',
            str(port),
            '--base-url',
            base_url,
            '--token-password',
            token,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
