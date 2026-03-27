from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.models import Node
from bulletjournal.execution.runner import WorkerRunner
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.run_service import RunService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def publish(self, *args, **kwargs) -> None:
        _ = (args, kwargs)


def _append_sample_node(project_service: ProjectService) -> None:
    graph = project_service.graph()
    graph.nodes.append(
        Node(
            id='sample_node',
            kind=NodeKind.NOTEBOOK,
            title='Sample Node',
            path='notebooks/sample_node.py',
            template=None,
            ui={'hidden_inputs': []},
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
            "    artifacts.push(1, name='value', data_type=int, is_output=True)\n"
            '    return\n'
        ),
        encoding='utf-8',
    )
    return notebook_path


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

    assert result['status'] == 'succeeded'
    assert captured_meta is not None
    assert captured_meta['status'] == 'running'
    assert captured_meta['stdout'] == {'text': 'live stdout\n', 'truncated': False}
    assert captured_meta['stderr'] == {'text': 'live stderr\n', 'truncated': False}


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

    assert result['status'] == 'succeeded'
    assert captured_meta is not None
    assert captured_meta['status'] == 'running'
    assert captured_meta['stdout'] == {'text': '', 'truncated': False}
    assert captured_meta['stderr'] == {'text': '', 'truncated': False}


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

    assert result['status'] == 'failed'
    assert result['node_results']['error'] == 'Managed run log file(s) missing for node `sample_node`: stdout.'
