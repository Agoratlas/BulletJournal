from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

import bulletjournal.cli.dev as dev_module
import bulletjournal.cli.doctor as doctor_module
import bulletjournal.cli.init_project as init_project_module
import bulletjournal.cli.rebuild_state as rebuild_state_module
import bulletjournal.cli.start as start_module
import bulletjournal.cli.validate_templates as validate_templates_module
from bulletjournal.config import ServerConfig


cli_app = importlib.import_module('bulletjournal.cli.app')


class DummyParser:
    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args

    def parse_args(self) -> argparse.Namespace:
        return self._args

    def error(self, message: str) -> None:
        raise RuntimeError(message)


def test_build_parser_parses_supported_commands() -> None:
    parser = cli_app.build_parser()

    init_args = parser.parse_args(['init', 'demo', '--title', 'Demo'])
    start_args = parser.parse_args(['start', 'demo', '--open'])
    dev_args = parser.parse_args(['dev', 'demo'])
    doctor_args = parser.parse_args(['doctor', 'demo'])
    validate_args = parser.parse_args(['validate-templates'])
    rebuild_args = parser.parse_args(['rebuild-state', 'demo'])

    assert init_args.command == 'init'
    assert init_args.title == 'Demo'
    assert start_args.open is True
    assert dev_args.command == 'dev'
    assert doctor_args.path == 'demo'
    assert validate_args.path is None
    assert rebuild_args.command == 'rebuild-state'


def test_app_starts_current_project_when_no_command(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = DummyParser(argparse.Namespace(command=None))
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(cli_app, 'build_parser', lambda: parser)
    monkeypatch.setattr(cli_app, 'is_project_root', lambda path: True)
    monkeypatch.setattr(cli_app, 'start_server', lambda path, open_browser=False: calls.append((path, open_browser)))

    cli_app.app()

    assert calls == [(str(Path('.').resolve()), False)]


def test_app_errors_when_no_command_outside_project(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = DummyParser(argparse.Namespace(command=None))

    monkeypatch.setattr(cli_app, 'build_parser', lambda: parser)
    monkeypatch.setattr(cli_app, 'is_project_root', lambda path: False)

    with pytest.raises(RuntimeError, match='No command provided'):
        cli_app.app()


def test_app_dispatches_init_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parser = DummyParser(argparse.Namespace(command='init', path='demo', title='Demo'))

    monkeypatch.setattr(cli_app, 'build_parser', lambda: parser)
    monkeypatch.setattr(cli_app, 'init_project', lambda path, title=None: Path('/tmp/demo'))

    cli_app.app()

    assert capsys.readouterr().out.strip() == '/tmp/demo'


def test_app_dispatches_start_and_dev_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    start_parser = DummyParser(argparse.Namespace(command='start', path='demo', open=True))
    dev_parser = DummyParser(argparse.Namespace(command='dev', path='workspace', open=False))
    start_calls: list[tuple[str, bool]] = []
    dev_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(cli_app, 'start_server', lambda path, open_browser=False: start_calls.append((path, open_browser)))
    monkeypatch.setattr(cli_app, 'dev_server', lambda path, open_browser=False: dev_calls.append((path, open_browser)))

    monkeypatch.setattr(cli_app, 'build_parser', lambda: start_parser)
    cli_app.app()

    monkeypatch.setattr(cli_app, 'build_parser', lambda: dev_parser)
    cli_app.app()

    assert start_calls == [('demo', True)]
    assert dev_calls == [('workspace', False)]


def test_app_prints_json_for_health_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    doctor_parser = DummyParser(argparse.Namespace(command='doctor', path='demo'))
    validate_parser = DummyParser(argparse.Namespace(command='validate-templates', path='templates'))
    rebuild_parser = DummyParser(argparse.Namespace(command='rebuild-state', path='demo'))

    monkeypatch.setattr(cli_app, 'doctor', lambda path: {'ok': True, 'path': path})
    monkeypatch.setattr(cli_app, 'validate_templates', lambda path: [{'path': path, 'issues': []}])
    monkeypatch.setattr(cli_app, 'rebuild_state', lambda path: {'project': path})

    monkeypatch.setattr(cli_app, 'build_parser', lambda: doctor_parser)
    cli_app.app()
    monkeypatch.setattr(cli_app, 'build_parser', lambda: validate_parser)
    cli_app.app()
    monkeypatch.setattr(cli_app, 'build_parser', lambda: rebuild_parser)
    cli_app.app()

    output = capsys.readouterr().out
    expected = (
        json.dumps({'ok': True, 'path': 'demo'}, indent=2, sort_keys=True)
        + '\n'
        + json.dumps([{'path': 'templates', 'issues': []}], indent=2, sort_keys=True)
        + '\n'
        + json.dumps({'project': 'demo'}, indent=2, sort_keys=True)
        + '\n'
    )
    assert output == expected


def test_app_errors_on_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = DummyParser(argparse.Namespace(command='mystery'))

    monkeypatch.setattr(cli_app, 'build_parser', lambda: parser)

    with pytest.raises(RuntimeError, match="Unknown command 'mystery'"):
        cli_app.app()


def test_start_server_builds_app_and_opens_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    import bulletjournal.api.app as api_app_module

    timer_delays: list[float] = []
    opened_urls: list[str] = []
    uvicorn_calls: list[tuple[object, str, int, bool]] = []
    created: dict[str, object] = {}

    class FakeTimer:
        def __init__(self, delay: float, callback) -> None:
            timer_delays.append(delay)
            self._callback = callback

        def start(self) -> None:
            self._callback()

    def fake_create_app(*, project_path: Path, server_config: ServerConfig):
        created['project_path'] = project_path
        created['server_config'] = server_config
        return 'fake-app'

    monkeypatch.setattr(api_app_module, 'create_app', fake_create_app)
    monkeypatch.setattr(start_module.threading, 'Timer', FakeTimer)
    monkeypatch.setattr(start_module.webbrowser, 'open', lambda url: opened_urls.append(url))
    monkeypatch.setattr(
        start_module.uvicorn,
        'run',
        lambda app, host, port, reload: uvicorn_calls.append((app, host, port, reload)),
    )

    start_module.start_server(
        'demo',
        host='0.0.0.0',
        port=9000,
        open_browser=True,
        reload=True,
        dev_frontend_url='http://127.0.0.1:5173',
    )

    assert created['project_path'] == Path('demo').resolve()
    assert created['server_config'] == ServerConfig(
        host='0.0.0.0',
        port=9000,
        open_browser=True,
        reload=True,
        dev_frontend_url='http://127.0.0.1:5173',
    )
    assert timer_delays == [1.0]
    assert opened_urls == ['http://0.0.0.0:9000']
    assert uvicorn_calls == [('fake-app', '0.0.0.0', 9000, True)]


def test_dev_server_prefers_pnpm_and_terminates_running_vite(monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls: list[tuple[list[str], Path]] = []
    start_calls: list[tuple[str | None, bool, bool, str | None]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

    process = FakeProcess()

    monkeypatch.setattr(dev_module.shutil, 'which', lambda name: '/usr/bin/pnpm' if name == 'pnpm' else None)
    monkeypatch.setattr(dev_module.subprocess, 'Popen', lambda command, cwd: popen_calls.append((command, cwd)) or process)
    monkeypatch.setattr(
        dev_module,
        'start_server',
        lambda path, open_browser=False, reload=False, dev_frontend_url=None: start_calls.append(
            (path, open_browser, reload, dev_frontend_url)
        ),
    )

    dev_module.dev_server('demo', open_browser=True)

    assert popen_calls
    assert popen_calls[0][0][:2] == ['pnpm', 'dev']
    assert start_calls == [('demo', True, True, 'http://127.0.0.1:5173')]
    assert process.terminated is True


def test_dev_server_uses_corepack_when_pnpm_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls: list[list[str]] = []

    class FakeProcess:
        def poll(self):
            return 0

        def terminate(self) -> None:
            raise AssertionError('terminate should not be called')

    def fake_which(name: str) -> str | None:
        if name == 'pnpm':
            return None
        if name == 'corepack':
            return '/usr/bin/corepack'
        return None

    monkeypatch.setattr(dev_module.shutil, 'which', fake_which)
    monkeypatch.setattr(dev_module.subprocess, 'Popen', lambda command, cwd: popen_calls.append(command) or FakeProcess())
    monkeypatch.setattr(dev_module, 'start_server', lambda path, **kwargs: None)

    dev_module.dev_server('demo')

    assert popen_calls == [['corepack', 'pnpm', 'dev', '--host', '127.0.0.1', '--port', '5173']]


def test_dev_server_skips_vite_when_no_package_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    start_calls: list[dict[str, object]] = []

    monkeypatch.setattr(dev_module.shutil, 'which', lambda name: None)
    monkeypatch.setattr(dev_module.subprocess, 'Popen', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unexpected Popen')))
    monkeypatch.setattr(dev_module, 'start_server', lambda path, **kwargs: start_calls.append({'path': path, **kwargs}))

    dev_module.dev_server('demo')

    assert start_calls == [
        {
            'path': 'demo',
            'open_browser': False,
            'reload': True,
            'dev_frontend_url': 'http://127.0.0.1:5173',
        }
    ]


def test_doctor_reports_available_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module, 'is_project_root', lambda path: True)

    def fake_find_spec(name: str):
        return None if name == 'pyarrow' else SimpleNamespace(name=name)

    monkeypatch.setattr(importlib.util, 'find_spec', fake_find_spec)

    result = doctor_module.doctor('demo')

    assert result == {
        'project_root': True,
        'fastapi': True,
        'marimo': True,
        'pandas': True,
        'pyarrow': False,
        'ok': False,
        'path': str(Path('demo').resolve()),
    }


def test_init_project_returns_initialized_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        init_project_module,
        'init_project_root',
        lambda path, title=None: SimpleNamespace(root=Path('/tmp/bj-project')),
    )

    assert init_project_module.init_project('demo', title='Demo') == Path('/tmp/bj-project')


def test_rebuild_state_opens_project_and_reparses(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Path] = []
    reparsed: list[bool] = []

    class FakeProjectService:
        def open_project(self, path: Path) -> dict[str, object]:
            opened.append(path)
            return {'project': 'demo'}

        def reparse_all_notebooks(self) -> None:
            reparsed.append(True)

    container = SimpleNamespace(project_service=FakeProjectService())
    monkeypatch.setattr(rebuild_state_module, 'ServiceContainer', lambda: container)

    result = rebuild_state_module.rebuild_state('demo')

    assert result == {'project': 'demo'}
    assert opened == [Path('demo').resolve()]
    assert reparsed == [True]


def test_validate_templates_uses_builtin_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    builtin_root = tmp_path / 'builtin'
    nested = builtin_root / 'nested'
    pipeline_root = tmp_path / 'pipelines'
    nested.mkdir(parents=True)
    pipeline_root.mkdir()
    notebook_a = builtin_root / 'a.py'
    notebook_b = nested / 'b.py'
    pipeline = pipeline_root / 'flow.json'
    for path in (notebook_a, notebook_b, pipeline):
        path.write_text('', encoding='utf-8')

    calls: list[tuple[Path, dict[str, Path]]] = []

    monkeypatch.setattr(validate_templates_module, 'BUILTIN_NOTEBOOK_TEMPLATE_ROOT', builtin_root)
    monkeypatch.setattr(validate_templates_module, 'builtin_templates', lambda: [notebook_a, notebook_b])
    monkeypatch.setattr(validate_templates_module, 'builtin_pipeline_templates', lambda: [pipeline])
    monkeypatch.setattr(
        validate_templates_module,
        'validate_template',
        lambda path, *, notebook_paths_by_ref: calls.append((path, notebook_paths_by_ref.copy())) or [],
    )

    results = validate_templates_module.validate_templates()

    assert [item['path'] for item in results] == [str(notebook_a), str(notebook_b), str(pipeline)]
    assert calls[0][1] == {'a.py': notebook_a, 'nested/b.py': notebook_b}


def test_validate_templates_supports_custom_builtin_and_pipeline_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / 'templates'
    notebook_root = root / 'builtin'
    pipeline_root = root / 'pipelines'
    notebook_root.mkdir(parents=True)
    pipeline_root.mkdir()
    notebook = notebook_root / 'demo.py'
    pipeline = pipeline_root / 'flow.json'
    notebook.write_text('', encoding='utf-8')
    pipeline.write_text('', encoding='utf-8')

    calls: list[tuple[Path, dict[str, Path]]] = []
    monkeypatch.setattr(
        validate_templates_module,
        'validate_template',
        lambda path, *, notebook_paths_by_ref: calls.append((path, notebook_paths_by_ref.copy())) or [],
    )

    results = validate_templates_module.validate_templates(str(root))

    assert [item['path'] for item in results] == [str(notebook), str(pipeline)]
    assert calls[0][1] == {'demo.py': notebook}


def test_validate_templates_supports_flat_custom_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / 'templates'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    notebook = root / 'demo.py'
    pipeline = nested / 'flow.json'
    notebook.write_text('', encoding='utf-8')
    pipeline.write_text('', encoding='utf-8')

    calls: list[dict[str, Path]] = []
    monkeypatch.setattr(
        validate_templates_module,
        'validate_template',
        lambda path, *, notebook_paths_by_ref: calls.append(notebook_paths_by_ref.copy()) or [],
    )

    validate_templates_module.validate_templates(str(root))

    assert calls[0] == {'demo.py': notebook}
    assert calls[1] == {'demo.py': notebook}


def test_package_main_invokes_cli_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_app_module = importlib.import_module('bulletjournal.cli.app')

    called: list[bool] = []
    monkeypatch.setattr(cli_app_module, 'app', lambda: called.append(True))

    runpy.run_module('bulletjournal.__main__', run_name='__main__')

    assert called == [True]
