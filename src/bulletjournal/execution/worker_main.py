from __future__ import annotations

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
from bulletjournal.storage.atomic_write import atomic_write_text


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
    captured_warnings: list[dict[str, object]] = []
    manifest: RunManifest | None = None
    try:
        manifest_path = Path(args[0])
        manifest = RunManifest.from_dict(json.loads(manifest_path.read_text(encoding='utf-8')))
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
            defer_publication=True,
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
            context.commit_publication()
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
        if context is not None:
            context.abandon_publication()
        payload = {
            'status': 'error',
            'error': str(exc),
            'traceback': traceback.format_exc(),
            'outputs': [] if context is None else context.pushed_outputs,
            'assets': [] if context is None else context.pushed_assets,
        }
        _write_result(manifest, payload)
        return 1
    payload = {'status': 'ok', 'outputs': context.pushed_outputs, 'assets': context.pushed_assets}
    if captured_warnings:
        payload['warnings'] = captured_warnings
    _write_result(manifest, payload)
    return 0


def _write_result(manifest: RunManifest | None, payload: dict[str, object]) -> None:
    if manifest is None or manifest.result_path is None:
        raise RuntimeError('Worker manifest does not define a result path.')
    atomic_write_text(Path(manifest.result_path), json.dumps(payload))


if __name__ == '__main__':
    raise SystemExit(main())
