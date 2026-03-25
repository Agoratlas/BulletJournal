from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pandas as pd
import pytest

import bulletjournal
import bulletjournal.api as bulletjournal_api
import bulletjournal.cli as bulletjournal_cli
import bulletjournal.runtime.artifacts as runtime_artifacts
import bulletjournal.runtime.file_artifacts as file_artifacts
import bulletjournal.storage as bulletjournal_storage
import bulletjournal.templates as bulletjournal_templates
import bulletjournal.templates.builtin_provider as builtin_template_provider
import bulletjournal.templates.registry as template_registry
from bulletjournal.domain.enums import ArtifactRole
from bulletjournal.execution import marimo_adapter
from bulletjournal.templates.builtin_provider import FilesystemTemplateProvider


def test_lazy_package_exports_and_attribute_errors() -> None:
    import bulletjournal.api.app as api_app_module
    import bulletjournal.storage.graph_store as graph_store_module
    import bulletjournal.storage.object_store as object_store_module
    import bulletjournal.storage.project_fs as project_fs_module
    import bulletjournal.storage.state_db as state_db_module
    import bulletjournal.runtime as runtime_package
    import bulletjournal.runtime.artifacts as runtime_artifacts_module

    assert bulletjournal.create_app is api_app_module.create_app
    assert bulletjournal_api.create_app is api_app_module.create_app
    assert bulletjournal_cli.app is importlib.import_module('bulletjournal.cli.app').app
    assert bulletjournal_templates.builtin_templates is template_registry.builtin_templates
    assert bulletjournal_templates.builtin_pipeline_templates is template_registry.builtin_pipeline_templates
    assert bulletjournal_storage.GraphStore is graph_store_module.GraphStore
    assert bulletjournal_storage.ObjectStore is object_store_module.ObjectStore
    assert bulletjournal_storage.ProjectPaths is project_fs_module.ProjectPaths
    assert bulletjournal_storage.StateDB is state_db_module.StateDB
    assert bulletjournal_storage.init_project_root is project_fs_module.init_project_root
    assert bulletjournal_storage.is_project_root is project_fs_module.is_project_root
    assert runtime_package.artifacts is runtime_artifacts_module

    with pytest.raises(AttributeError):
        getattr(bulletjournal, 'missing')
    with pytest.raises(AttributeError):
        getattr(bulletjournal_api, 'missing')
    with pytest.raises(AttributeError):
        getattr(bulletjournal_storage, 'missing')


def test_runtime_artifacts_module_exposes_helper_functions_after_submodule_import() -> None:
    runtime_package = importlib.import_module('bulletjournal.runtime')
    runtime_module = importlib.import_module('bulletjournal.runtime.artifacts')

    imported = getattr(runtime_package, 'artifacts')

    assert callable(runtime_module.pull)
    assert callable(runtime_module.pull_file)
    assert callable(runtime_module.push)
    assert callable(runtime_module.push_file)
    assert imported is runtime_module


def test_template_registry_discovers_builtin_and_pipeline_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir = tmp_path / 'templates'
    builtin_dir = package_dir / 'builtin'
    pipeline_dir = package_dir / 'pipelines'
    pycache_dir = builtin_dir / '__pycache__'
    builtin_dir.mkdir(parents=True)
    pipeline_dir.mkdir(parents=True)
    pycache_dir.mkdir(parents=True)

    builtin_file = builtin_dir / 'example.py'
    skipped_file = pycache_dir / 'skip.py'
    pipeline_file = pipeline_dir / 'flow.json'
    for path in (builtin_file, skipped_file, pipeline_file):
        path.write_text('', encoding='utf-8')

    fake_registry = package_dir / 'registry.py'
    fake_registry.write_text('', encoding='utf-8')
    monkeypatch.setattr(
        template_registry,
        'builtin_provider',
        lambda: FilesystemTemplateProvider(
            provider_name='builtin',
            notebook_root=builtin_dir,
            pipeline_root=pipeline_dir,
            origin_revision='builtin@0.1.0',
        ),
    )

    assert template_registry.builtin_templates() == [builtin_file]
    assert template_registry.builtin_pipeline_templates() == [pipeline_file]


def test_filesystem_template_provider_supports_loader_api(tmp_path: Path) -> None:
    notebook_root = tmp_path / 'builtin'
    pipeline_root = tmp_path / 'pipelines'
    notebook_root.mkdir(parents=True)
    pipeline_root.mkdir(parents=True)
    notebook = notebook_root / 'sample.py'
    pipeline = pipeline_root / 'flow.json'
    notebook.write_text('import marimo\napp = marimo.App()\n', encoding='utf-8')
    pipeline.write_text('{"nodes": [], "edges": [], "layout": []}\n', encoding='utf-8')

    provider = builtin_template_provider.FilesystemTemplateProvider(
        provider_name='builtin',
        notebook_root=notebook_root,
        pipeline_root=pipeline_root,
        origin_revision='builtin@0.1.0',
    )

    assert provider.provider_revision == 'builtin@0.1.0'
    assert provider.load_notebook_template('sample') == 'import marimo\napp = marimo.App()\n'
    assert provider.load_pipeline_template('flow') == '{"nodes": [], "edges": [], "layout": []}\n'


def test_artifacts_api_delegates_to_runtime_context(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {'value': 7, 'artifact_hash': 'abc', 'state': 'ready', 'warnings': [], 'upstream_code_hash': 'upstream'}
    file_metadata = {
        'path': Path('/tmp/data.csv'),
        'artifact_hash': 'file',
        'state': 'ready',
        'warnings': [],
        'upstream_code_hash': 'upstream',
    }
    calls: list[tuple[str, object]] = []

    class FakeContext:
        def validate_pull_contract(self, *, name: str, data_type: str) -> None:
            calls.append(('validate', (name, data_type)))

        def resolve_pull(self, name: str) -> dict[str, object]:
            calls.append(('resolve_pull', name))
            return metadata

        def resolve_pull_file(self, *, name: str, allow_missing: bool = False) -> dict[str, object]:
            calls.append(('resolve_pull_file', (name, allow_missing)))
            return file_metadata

        def record_pull(self, name: str, payload: dict[str, object]) -> None:
            calls.append(('record_pull', (name, payload)))

        def finalize_value_push(self, *, name: str, value, data_type: str, role: ArtifactRole) -> None:
            calls.append(('push', (name, value, data_type, role)))

    context = FakeContext()
    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: context)

    value = runtime_artifacts.pull(name='count', data_type=int, default=10, description='ignored')
    file_path = runtime_artifacts.pull_file(name='dataset', allow_missing=True, description='ignored')
    runtime_artifacts.push(42, name='result', data_type=int, is_output=True, description='ignored')
    handle = runtime_artifacts.push_file(name='report', extension='.txt', is_output=False)

    assert value == 7
    assert file_path == '/tmp/data.csv'
    assert handle.name == 'report'
    assert handle.extension == '.txt'
    assert handle.role == ArtifactRole.ASSET
    assert calls == [
        ('validate', ('count', 'int')),
        ('resolve_pull', 'count'),
        ('record_pull', ('count', metadata)),
        ('resolve_pull_file', ('dataset', True)),
        ('record_pull', ('dataset', file_metadata)),
        ('push', ('result', 42, 'int', ArtifactRole.OUTPUT)),
    ]


def test_artifacts_pull_file_returns_none_for_optional_missing_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        'path': None,
        'artifact_hash': 'missing',
        'state': 'ready',
        'warnings': [],
        'upstream_code_hash': 'default',
    }
    calls: list[tuple[str, object]] = []

    class FakeContext:
        def resolve_pull_file(self, *, name: str, allow_missing: bool = False) -> dict[str, object]:
            calls.append(('resolve_pull_file', (name, allow_missing)))
            return metadata

        def record_pull(self, name: str, payload: dict[str, object]) -> None:
            calls.append(('record_pull', (name, payload)))

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    file_path = runtime_artifacts.pull_file(name='dataset', allow_missing=True)

    assert file_path is None
    assert calls == [
        ('resolve_pull_file', ('dataset', True)),
        ('record_pull', ('dataset', metadata)),
    ]


def test_normalize_runtime_type_covers_known_and_fallback_types() -> None:
    GraphType = type('Graph', (), {'__module__': 'networkx.classes.graph'})
    UnknownType = type('CustomThing', (), {'__module__': 'myapp.models'})

    assert runtime_artifacts._normalize_runtime_type('int') == 'int'
    assert runtime_artifacts._normalize_runtime_type(int) == 'int'
    assert runtime_artifacts._normalize_runtime_type(pd.DataFrame) == 'pandas.DataFrame'
    assert runtime_artifacts._normalize_runtime_type(pd.Series) == 'pandas.Series'
    assert runtime_artifacts._normalize_runtime_type(GraphType) == 'networkx.Graph'
    assert runtime_artifacts._normalize_runtime_type(UnknownType) == 'object'


def test_file_push_handle_finalizes_and_cleans_up_temp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_file = tmp_path / 'artifact.txt'
    finalized: list[tuple[str, Path, ArtifactRole]] = []

    class FakeObjectStore:
        def create_temp_file(self, extension: str) -> Path:
            assert extension == '.txt'
            return temp_file

    class FakeContext:
        object_store = FakeObjectStore()

        def finalize_file_push(self, *, name: str, temp_path: Path, role: ArtifactRole) -> None:
            finalized.append((name, temp_path, role))

    monkeypatch.setattr(file_artifacts, 'current_runtime_context', lambda: FakeContext())

    with file_artifacts.FilePushHandle(name='report', role=ArtifactRole.OUTPUT, extension='.txt') as path:
        path.write_text('hello', encoding='utf-8')

    assert finalized == [('report', temp_file, ArtifactRole.OUTPUT)]
    assert temp_file.exists() is False


def test_file_push_handle_cleans_up_without_finalize_on_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_file = tmp_path / 'artifact.txt'
    finalized: list[str] = []

    class FakeObjectStore:
        def create_temp_file(self, extension: str) -> Path:
            return temp_file

    class FakeContext:
        object_store = FakeObjectStore()

        def finalize_file_push(self, *, name: str, temp_path: Path, role: ArtifactRole) -> None:
            finalized.append(name)

    monkeypatch.setattr(file_artifacts, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(ValueError, match='boom'):
        with file_artifacts.FilePushHandle(name='report', role=ArtifactRole.ASSET, extension='.txt') as path:
            path.write_text('hello', encoding='utf-8')
            raise ValueError('boom')

    assert finalized == []
    assert temp_file.exists() is False


def test_load_notebook_module_imports_python_file(tmp_path: Path) -> None:
    notebook = tmp_path / 'sample_notebook.py'
    notebook.write_text('value = 3\n', encoding='utf-8')

    module = marimo_adapter.load_notebook_module(notebook)

    assert module.value == 3


def test_load_notebook_module_rejects_missing_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    notebook = tmp_path / 'sample_notebook.py'

    monkeypatch.setattr(
        marimo_adapter.importlib.util,
        'spec_from_file_location',
        lambda name, path: SimpleNamespace(loader=None),
    )

    with pytest.raises(RuntimeError, match='Cannot load notebook module'):
        marimo_adapter.load_notebook_module(notebook)


def test_execute_notebook_requires_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    notebook = tmp_path / 'sample_notebook.py'

    monkeypatch.setattr(marimo_adapter, 'load_notebook_module', lambda path: SimpleNamespace())

    with pytest.raises(RuntimeError, match='does not define `app`'):
        marimo_adapter.execute_notebook(notebook)


def test_execute_notebook_sets_progress_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    notebook = tmp_path / 'sample_notebook.py'
    progress_path = tmp_path / 'progress.json'

    class FakeApp:
        def run(self) -> dict[str, object]:
            assert marimo_adapter.os.environ['BULLETJOURNAL_PROGRESS_PATH'] == str(progress_path)
            return {'ok': True}

    monkeypatch.setattr(marimo_adapter, 'load_notebook_module', lambda path: SimpleNamespace(app=FakeApp()))

    result = marimo_adapter.execute_notebook(notebook, progress_path=progress_path)

    assert result == {'result': {'ok': True}}
    assert 'BULLETJOURNAL_PROGRESS_PATH' not in marimo_adapter.os.environ


def test_launch_editor_invokes_marimo_with_expected_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    notebook = tmp_path / 'sample_notebook.py'
    popen_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        marimo_adapter.subprocess,
        'Popen',
        lambda command, stdout, stderr, text, env: (
            popen_calls.append(
                {
                    'command': command,
                    'stdout': stdout,
                    'stderr': stderr,
                    'text': text,
                    'env': env,
                }
            )
            or 'process'
        ),
    )

    process = marimo_adapter.launch_editor(
        notebook,
        host='127.0.0.1',
        port=2718,
        base_url='/editor',
        environment={'EXTRA_FLAG': '1'},
    )

    assert process == 'process'
    assert popen_calls[0]['command'] == [
        marimo_adapter.sys.executable,
        '-m',
        'marimo',
        'edit',
        str(notebook),
        '--headless',
        '--host',
        '127.0.0.1',
        '--port',
        '2718',
        '--base-url',
        '/editor',
        '--no-token',
    ]
    assert popen_calls[0]['text'] is True
    env = cast(dict[str, str], popen_calls[0]['env'])
    assert env['EXTRA_FLAG'] == '1'
