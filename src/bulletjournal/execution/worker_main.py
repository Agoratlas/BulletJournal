from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
import warnings
from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.domain.models import AssetDeclaration, Port
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.marimo_adapter import execute_notebook
from bulletjournal.runtime.context import Binding, RuntimeContext, activate_runtime_context


class _TeeWriter:
    def __init__(self, *targets) -> None:
        self._targets = targets
        primary = targets[0] if targets else None
        self.encoding = getattr(primary, 'encoding', 'utf-8')
        self.errors = getattr(primary, 'errors', None)
        self.newlines = getattr(primary, 'newlines', None)

    def write(self, value: str) -> int:
        written = 0
        for target in self._targets:
            written = target.write(value)
            flush = getattr(target, 'flush', None)
            if callable(flush):
                flush()
        return written

    def flush(self) -> None:
        for target in self._targets:
            target.flush()

    def isatty(self) -> bool:
        return any(bool(getattr(target, 'isatty', lambda: False)()) for target in self._targets)

    def fileno(self) -> int:
        for target in self._targets:
            fileno = getattr(target, 'fileno', None)
            if fileno is None:
                continue
            try:
                return fileno()
            except (OSError, io.UnsupportedOperation):
                continue
        raise io.UnsupportedOperation('fileno')

    def writable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return all(bool(getattr(target, 'closed', False)) for target in self._targets)

    def __getattr__(self, name: str):
        for target in self._targets:
            if hasattr(target, name):
                return getattr(target, name)
        raise AttributeError(name)


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
    _ = notebook_path
    from marimo._runtime.app.script_runner import AppScriptRunner

    original_run_synchronous = getattr(
        AppScriptRunner,
        '_bulletjournal_original_run_synchronous',
        AppScriptRunner._run_synchronous,
    )
    original_run_asynchronous = getattr(
        AppScriptRunner,
        '_bulletjournal_original_run_asynchronous',
        AppScriptRunner._run_asynchronous,
    )

    AppScriptRunner._bulletjournal_original_run_synchronous = original_run_synchronous
    AppScriptRunner._bulletjournal_original_run_asynchronous = original_run_asynchronous

    def _decorate_scheduler(runner: object) -> None:
        scheduler = getattr(runner, '_scheduler', None)
        if scheduler is None:
            raise RuntimeError('Unsupported marimo AppScriptRunner internals: missing `_scheduler`.')
        pop_cell = getattr(scheduler, 'pop_cell', None)
        cells_to_run = getattr(scheduler, 'cells_to_run', None)
        if not callable(pop_cell) or cells_to_run is None:
            raise RuntimeError('Unsupported marimo scheduler internals: expected `pop_cell()` and `cells_to_run`.')
        if getattr(scheduler, '_bulletjournal_progress_wrapped', False):
            return

        total_cells = len(cells_to_run)
        execution_index = 0

        def wrapped_pop_cell():
            nonlocal execution_index
            cell_id = pop_cell()
            execution_index += 1
            graph = runner.app.graph
            cell_impl = graph.cells[cell_id]
            _write_progress(
                progress_path,
                {
                    'cell_id': str(cell_id),
                    'cell_number': execution_index,
                    'total_cells': total_cells,
                    'cell_code': cell_impl.code,
                },
            )
            return cell_id

        scheduler.pop_cell = wrapped_pop_cell
        scheduler._bulletjournal_progress_wrapped = True

    def patched_run_synchronous(self, post_execute_hooks):
        _decorate_scheduler(self)
        return original_run_synchronous(self, post_execute_hooks)

    async def patched_run_asynchronous(self, post_execute_hooks):
        _decorate_scheduler(self)
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
    captured_warnings: list[dict[str, object]] = []
    stdout_log_handle = None
    stderr_log_handle = None
    try:
        manifest_path = Path(args[0])
        manifest = RunManifest.from_dict(json.loads(manifest_path.read_text(encoding='utf-8')))
        stdout_log_path = Path(manifest.stdout_path) if manifest.stdout_path else None
        stderr_log_path = Path(manifest.stderr_path) if manifest.stderr_path else None
        if stdout_log_path is not None:
            stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_log_handle = stdout_log_path.open('w', encoding='utf-8', buffering=1)
        if stderr_log_path is not None:
            stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_log_handle = stderr_log_path.open('w', encoding='utf-8', buffering=1)
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
            asset_declarations = {
                name: AssetDeclaration(
                    node_id=value.get('node_id', manifest.node_id),
                    name=name,
                    title=value['title'],
                    description=value.get('description'),
                    declared_asset_type=value.get('declared_asset_type'),
                    declaration_index=int(value.get('declaration_index', 0)),
                )
                for name, value in manifest.assets.items()
            }
            context = RuntimeContext(
                project_root=Path(manifest.project_root),
                node_id=manifest.node_id,
                run_id=manifest.run_id,
                source_hash=manifest.source_hash,
                lineage_mode=LineageMode(manifest.lineage_mode),
                bindings=bindings,
                outputs=outputs,
                asset_declarations=asset_declarations,
            )
            progress_path = Path(manifest.progress_path) if manifest.progress_path else None
            _install_script_runner_progress_hooks(
                notebook_path=Path(manifest.notebook_path),
                progress_path=progress_path,
            )
            with warnings.catch_warnings(record=True) as runtime_warnings:
                warnings.simplefilter('always')
                with activate_runtime_context(context):
                    execute_notebook(Path(manifest.notebook_path), progress_path=progress_path)
                captured_warnings = [
                    {
                        'message': str(item.message),
                        'category': item.category.__name__,
                        'filename': item.filename,
                        'lineno': item.lineno,
                    }
                    for item in runtime_warnings
                ]
    except Exception as exc:
        payload = {
            'status': 'error',
            'error': str(exc),
            'traceback': traceback.format_exc(),
            'outputs': [] if context is None else context.pushed_outputs,
            'assets': [] if context is None else context.pushed_assets,
        }
        if captured_warnings:
            payload['warnings'] = captured_warnings
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
    payload = {'status': 'ok', 'outputs': context.pushed_outputs, 'assets': context.pushed_assets}
    if captured_warnings:
        payload['warnings'] = captured_warnings
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
