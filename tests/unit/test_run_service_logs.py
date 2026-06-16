from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from bulletjournal.domain.enums import NodeKind, ValidationSeverity
from bulletjournal.domain.models import Node
from bulletjournal.execution.runner import WorkerRunner
from bulletjournal.execution.worker_main import _TeeWriter
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.run_service import RunService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def __init__(self) -> None:
        self.events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def publish(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class _FlushTrackingTarget:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flush_count = 0

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        self.flush_count += 1


def _wait_for_run_status(
    project_service: ProjectService, run_id: str, expected: str, *, timeout: float = 5.0
) -> dict[str, object]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = next(
            (
                entry
                for entry in project_service.require_project().state_db.list_run_records()
                if entry['run_id'] == run_id
            ),
            None,
        )
        if run is not None and run['status'] == expected:
            return run
        time.sleep(0.01)
    raise AssertionError(f'Run `{run_id}` did not reach status `{expected}` within {timeout} seconds.')


def _append_sample_node(project_service: ProjectService) -> None:
    graph = project_service.graph()
    graph.nodes.append(
        Node(
            id='sample_node',
            kind=NodeKind.NOTEBOOK,
            title='Sample Node',
            path='notebooks/sample_node.py',
            template=None,
            ui={},
        )
    )
    project_service.write_graph(graph)


def _write_sample_notebook(project_root: Path) -> Path:
    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    notebook_path.write_text(
        (
            'import marimo\n\n'
            'app = marimo.App()\n\n'
            'with app.setup:\n'
            '    from bulletjournal.runtime import artifacts\n\n'
            '@app.cell\n'
            'def _():\n'
            "    artifacts.push(1, name='value', data_type=int)\n"
            '    return\n'
        ),
        encoding='utf-8',
    )
    return notebook_path


def _write_asset_notebook(project_root: Path) -> Path:
    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    notebook_path.write_text(
        (
            'import marimo\n\n'
            'app = marimo.App()\n\n'
            'with app.setup:\n'
            '    import pandas as pd\n'
            '    from bulletjournal.runtime import assets\n\n'
            '@app.cell\n'
            'def _():\n'
            "    frame = pd.DataFrame({'value': [1, 2, 3]})\n"
            "    assets.push(assets.DataFrame(frame), name='table', title='Table', asset_type=assets.DataFrame)\n"
            '    return\n'
        ),
        encoding='utf-8',
    )
    return notebook_path


def test_worker_tee_writer_flushes_every_write() -> None:
    first = _FlushTrackingTarget()
    second = _FlushTrackingTarget()

    writer = _TeeWriter(first, second)
    written = writer.write('hello\n')

    assert written == len('hello\n')
    assert first.writes == ['hello\n']
    assert second.writes == ['hello\n']
    assert first.flush_count == 1
    assert second.flush_count == 1


def test_running_execution_metadata_retains_log_paths_for_live_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)
    run_service = RunService(project_service)
    project_service.run_service = run_service  # type: ignore[assignment]

    _append_sample_node(project_service)
    notebook_path = _write_sample_notebook(project_root)
    project_service.reparse_notebook_by_path(notebook_path)

    captured_meta = None

    def fake_worker_run(manifest, temp_dir, cancel_event, on_process_started, on_progress):
        _ = (temp_dir, cancel_event, on_process_started)
        assert manifest.stdout_path is not None
        assert manifest.stderr_path is not None
        stdout_path = project_root / 'temp' / 'execution_logs' / f'{manifest.run_id}_{manifest.node_id}.stdout.log'
        stderr_path = project_root / 'temp' / 'execution_logs' / f'{manifest.run_id}_{manifest.node_id}.stderr.log'
        stdout_path.write_text('live stdout\n', encoding='utf-8')
        stderr_path.write_text('live stderr\n', encoding='utf-8')
        on_progress({'cell_number': 1, 'total_cells': 1, 'cell_code': "print('x')", 'cell_id': 'cell-1'})
        nonlocal captured_meta
        captured_meta = project_service.require_project().state_db.list_orchestrator_execution_meta()['sample_node']
        return {'status': 'ok', 'outputs': [], 'progress': {'cell_number': 1, 'total_cells': 1}}

    run_service.worker_runner = cast(WorkerRunner, SimpleNamespace(run=fake_worker_run))

    result = run_service.start_node_run('sample_node', mode='run_all', action='use_stale')

    assert result['status'] == 'running'
    _wait_for_run_status(project_service, str(result['run_id']), 'succeeded')
    assert captured_meta is not None
    assert captured_meta['status'] == 'running'
    assert captured_meta['stdout'] == {'text': 'live stdout\n', 'truncated': False, 'size_bytes': 12}
    assert captured_meta['stderr'] == {'text': 'live stderr\n', 'truncated': False, 'size_bytes': 12}


def test_run_service_publishes_asset_version_events(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)
    run_service = RunService(project_service)
    project_service.run_service = run_service  # type: ignore[assignment]

    _append_sample_node(project_service)
    notebook_path = _write_asset_notebook(project_root)
    project_service.reparse_notebook_by_path(notebook_path)

    result = run_service.start_node_run('sample_node', mode='run_all', action='use_stale')

    assert result['status'] == 'running'
    _wait_for_run_status(project_service, str(result['run_id']), 'succeeded')
    assert any(
        args[0] == 'asset.version_created'
        and kwargs['payload']['asset_name'] == 'table'
        and kwargs['payload']['new_state'] == 'ready'
        for args, kwargs in event_service.events
    )


def test_run_service_syncs_editor_session_asset_updates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)
    run_service = RunService(project_service)
    project_service.run_service = run_service  # type: ignore[assignment]

    _append_sample_node(project_service)
    notebook_path = _write_asset_notebook(project_root)
    project_service.reparse_notebook_by_path(notebook_path)

    run_service._editor_session_output_snapshots['sample_node'] = run_service._output_snapshot_for_node('sample_node')

    result = run_service.start_node_run('sample_node', mode='run_all', action='use_stale')

    assert result['status'] == 'running'
    _wait_for_run_status(project_service, str(result['run_id']), 'succeeded')
    event_service.events.clear()

    run_service.sync_editor_session_outputs('sample_node')

    assert any(
        args[0] == 'asset.version_created'
        and kwargs['payload']['asset_name'] == 'table'
        and kwargs['payload']['new_state'] == 'ready'
        for args, kwargs in event_service.events
    )


def test_running_execution_metadata_exposes_empty_live_logs_when_worker_has_not_written_yet(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)
    run_service = RunService(project_service)
    project_service.run_service = run_service  # type: ignore[assignment]

    _append_sample_node(project_service)
    notebook_path = _write_sample_notebook(project_root)
    project_service.reparse_notebook_by_path(notebook_path)

    captured_meta = None

    def fake_worker_run(manifest, temp_dir, cancel_event, on_process_started, on_progress):
        _ = (temp_dir, cancel_event, on_process_started)
        on_progress({'cell_number': 1, 'total_cells': 1, 'cell_code': 'pass', 'cell_id': 'cell-1'})
        nonlocal captured_meta
        captured_meta = project_service.require_project().state_db.list_orchestrator_execution_meta()['sample_node']
        return {'status': 'ok', 'outputs': [], 'progress': {'cell_number': 1, 'total_cells': 1}}

    run_service.worker_runner = cast(WorkerRunner, SimpleNamespace(run=fake_worker_run))

    result = run_service.start_node_run('sample_node', mode='run_all', action='use_stale')

    assert result['status'] == 'running'
    _wait_for_run_status(project_service, str(result['run_id']), 'succeeded')
    assert captured_meta is not None
    assert captured_meta['status'] == 'running'
    assert captured_meta['stdout'] == {'text': '', 'truncated': False, 'size_bytes': 0}
    assert captured_meta['stderr'] == {'text': '', 'truncated': False, 'size_bytes': 0}


def test_managed_run_fails_if_execution_log_file_goes_missing(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)
    run_service = RunService(project_service)
    project_service.run_service = run_service  # type: ignore[assignment]

    _append_sample_node(project_service)
    notebook_path = _write_sample_notebook(project_root)
    project_service.reparse_notebook_by_path(notebook_path)

    def fake_worker_run(manifest, temp_dir, cancel_event, on_process_started, on_progress):
        _ = (temp_dir, cancel_event, on_process_started, on_progress)
        assert manifest.stdout_path is not None
        Path(manifest.stdout_path).unlink()
        return {'status': 'ok', 'outputs': []}

    run_service.worker_runner = cast(WorkerRunner, SimpleNamespace(run=fake_worker_run))

    result = run_service.start_node_run('sample_node', mode='run_all', action='use_stale')

    assert result['status'] == 'running'
    run = _wait_for_run_status(project_service, str(result['run_id']), 'failed')
    assert run['failure_json']['error'] == 'Managed run log file(s) missing for node `sample_node`: stdout.'


def test_record_notice_wraps_markdown_sensitive_values(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    event_service = _FakeEventService()
    project_service = ProjectService(event_service, TemplateService())
    project_service.open_project(project_root)

    notice = project_service.record_notice(
        issue_id='notice-1',
        node_id=None,
        severity=ValidationSeverity.ERROR,
        code='test_notice',
        message='Notebook notebook_2_copy failed because source_node/output_value is missing.',
    )

    assert notice['message'] == 'Notebook `notebook_2_copy` failed because `source_node/output_value` is missing.'
