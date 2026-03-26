from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import traceback
from collections import deque
from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.domain.models import Port
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.marimo_adapter import execute_notebook
from bulletjournal.parser.marimo_loader import iter_app_cells, load_module_ast
from bulletjournal.runtime.context import Binding, RuntimeContext, activate_runtime_context


class _TeeWriter:
    def __init__(self, *targets) -> None:
        self._targets = targets

    def write(self, value: str) -> int:
        written = 0
        for target in self._targets:
            written = target.write(value)
        return written

    def flush(self) -> None:
        for target in self._targets:
            target.flush()


def _write_progress(
    progress_path: Path | None,
    payload: dict[str, object],
) -> None:
    if progress_path is None:
        return
    progress_path.write_text(json.dumps(payload), encoding='utf-8')


def _install_script_runner_progress_hooks(
    *,
    notebook_path: Path,
    progress_path: Path | None,
) -> None:
    if progress_path is None:
        return
    from marimo._runtime.app.script_runner import AppScriptRunner

    module = load_module_ast(notebook_path)
    cell_order = iter_app_cells(module)
    cell_number_by_index = {index: index + 1 for index in range(len(cell_order))}
    total_cells = len(cell_order)

    original_run_synchronous = AppScriptRunner._run_synchronous
    original_run_asynchronous = AppScriptRunner._run_asynchronous

    class ProgressDeque(deque):
        def __init__(self, values: deque, runner: object) -> None:
            super().__init__(values)
            self._runner = runner
            self._execution_index = 0

        def popleft(self):  # type: ignore[override]
            cell_id = super().popleft()
            if total_cells > 0:
                cell_number = cell_number_by_index.get(self._execution_index)
                self._execution_index += 1
                graph = getattr(self._runner, 'app').graph
                cell_impl = graph.cells[cell_id]
                _write_progress(
                    progress_path,
                    {
                        'cell_id': str(cell_id),
                        'cell_number': cell_number,
                        'total_cells': total_cells,
                        'cell_code': cell_impl.code,
                    },
                )
            return cell_id

    def _decorate_queue(runner: object) -> deque:
        queue = getattr(runner, 'cells_to_run')
        if getattr(runner, '_bulletjournal_progress_wrapped', False):
            return queue
        wrapped_queue = ProgressDeque(queue, runner)
        setattr(runner, 'cells_to_run', wrapped_queue)
        setattr(runner, '_bulletjournal_progress_wrapped', True)
        return wrapped_queue

    def patched_run_synchronous(self, post_execute_hooks):
        _decorate_queue(self)
        return original_run_synchronous(self, post_execute_hooks)

    async def patched_run_asynchronous(self, post_execute_hooks):
        _decorate_queue(self)
        return await original_run_asynchronous(self, post_execute_hooks)

    AppScriptRunner._run_synchronous = patched_run_synchronous
    AppScriptRunner._run_asynchronous = patched_run_asynchronous


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        raise SystemExit('Usage: python -m bulletjournal.execution.worker_main <manifest.json>')
    context: RuntimeContext | None = None
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    stdout_log_handle = None
    stderr_log_handle = None
    try:
        manifest_path = Path(args[0])
        manifest = RunManifest.from_dict(json.loads(manifest_path.read_text(encoding='utf-8')))
        stdout_log_path = Path(manifest.stdout_path) if manifest.stdout_path else None
        stderr_log_path = Path(manifest.stderr_path) if manifest.stderr_path else None
        if stdout_log_path is not None:
            stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_log_handle = stdout_log_path.open('w', encoding='utf-8')
        if stderr_log_path is not None:
            stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_log_handle = stderr_log_path.open('w', encoding='utf-8')
        stdout_target = captured_stdout if stdout_log_handle is None else _TeeWriter(captured_stdout, stdout_log_handle)
        stderr_target = captured_stderr if stderr_log_handle is None else _TeeWriter(captured_stderr, stderr_log_handle)
        with contextlib.redirect_stdout(stdout_target), contextlib.redirect_stderr(stderr_target):
            bindings = {
                name: Binding(
                    source_node=value.get('source_node', ''),
                    source_artifact=value.get('source_artifact', ''),
                    data_type=value['data_type'],
                    default=value.get('default'),
                    has_default=bool(value.get('has_default', False)),
                )
                for name, value in manifest.bindings.items()
            }
            outputs = {
                name: Port(
                    name=name,
                    data_type=value['data_type'],
                    role=ArtifactRole(value['role']),
                    description=value.get('description'),
                    kind=value.get('kind', 'value'),
                    direction='output',
                )
                for name, value in manifest.outputs.items()
            }
            context = RuntimeContext(
                project_root=Path(manifest.project_root),
                node_id=manifest.node_id,
                run_id=manifest.run_id,
                source_hash=manifest.source_hash,
                lineage_mode=LineageMode(manifest.lineage_mode),
                bindings=bindings,
                outputs=outputs,
            )
            progress_path = Path(manifest.progress_path) if manifest.progress_path else None
            _install_script_runner_progress_hooks(
                notebook_path=Path(manifest.notebook_path),
                progress_path=progress_path,
            )
            with activate_runtime_context(context):
                execute_notebook(Path(manifest.notebook_path), progress_path=progress_path)
    except Exception as exc:  # noqa: BLE001
        payload = {
            'status': 'error',
            'error': str(exc),
            'traceback': traceback.format_exc(),
            'outputs': [] if context is None else context.pushed_outputs,
        }
        stdout_text = captured_stdout.getvalue()
        stderr_text = captured_stderr.getvalue()
        if stdout_text.strip():
            payload['stdout'] = stdout_text
        if stderr_text.strip():
            payload['stderr'] = stderr_text
        sys.stdout.write(json.dumps(payload))
        return 1
    finally:
        if stdout_log_handle is not None:
            stdout_log_handle.close()
        if stderr_log_handle is not None:
            stderr_log_handle.close()
    payload = {'status': 'ok', 'outputs': context.pushed_outputs}
    stdout_text = captured_stdout.getvalue()
    stderr_text = captured_stderr.getvalue()
    if stdout_text.strip():
        payload['stdout'] = stdout_text
    if stderr_text.strip():
        payload['stderr'] = stderr_text
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
