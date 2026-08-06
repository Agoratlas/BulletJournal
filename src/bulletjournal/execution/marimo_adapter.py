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


def execute_notebook(
    path: Path,
    *,
    progress_path: Path | None = None,
) -> dict[str, object]:
    module = load_notebook_module(path)
    app = getattr(module, 'app', None)
    if app is None:
        raise RuntimeError(f'Notebook {path} does not define `app`.')
    if progress_path is not None:
        os.environ['BULLETJOURNAL_PROGRESS_PATH'] = str(progress_path)
    from marimo._runtime.app.script_runner import AppScriptRunner
    from marimo._runtime.control_flow import MarimoStopError
    from marimo._runtime.exceptions import MarimoRuntimeException, unwrap_user_exception

    original_handle_run_result = AppScriptRunner._handle_run_result

    def fail_on_stop(self, cell_id, result, outputs) -> None:
        exception = result.exception
        if isinstance(exception, MarimoRuntimeException):
            unwrapped = unwrap_user_exception(exception, self.app.graph)
            if isinstance(unwrapped, MarimoStopError):
                raise RuntimeError('Notebook execution stopped by mo.stop().') from unwrapped
        original_handle_run_result(self, cell_id, result, outputs)

    AppScriptRunner._handle_run_result = fail_on_stop
    try:
        result = app.run()
    finally:
        AppScriptRunner._handle_run_result = original_handle_run_result
        if progress_path is not None:
            os.environ.pop('BULLETJOURNAL_PROGRESS_PATH', None)
    return {'result': result}


def launch_editor(
    path: Path,
    *,
    host: str,
    port: int,
    base_url: str,
    environment: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[2])
    existing_pythonpath = env.get('PYTHONPATH', '').strip()
    env['PYTHONPATH'] = src_path if not existing_pythonpath else f'{src_path}{os.pathsep}{existing_pythonpath}'
    if environment:
        env.update(environment)
    return subprocess.Popen(  # noqa: S603
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
            '--no-token',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
