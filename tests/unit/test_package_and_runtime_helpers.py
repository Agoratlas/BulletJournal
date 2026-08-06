from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pandas as pd
import pytest

import bulletjournal
import bulletjournal.api as bulletjournal_api
import bulletjournal.cli as bulletjournal_cli
import bulletjournal.execution.worker_main as worker_main
import bulletjournal.runtime.artifacts as runtime_artifacts
import bulletjournal.runtime.assets as runtime_assets
import bulletjournal.runtime.context as runtime_context
import bulletjournal.runtime.file_artifacts as file_artifacts
import bulletjournal.storage as bulletjournal_storage
import bulletjournal.templates as bulletjournal_templates
import bulletjournal.templates.builtin_provider as builtin_template_provider
import bulletjournal.templates.registry as template_registry
from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.execution import marimo_adapter
from bulletjournal.execution import runner as runner_module
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.templates.builtin_provider import FilesystemTemplateProvider


def test_lazy_package_exports_and_attribute_errors() -> None:
    import bulletjournal.api.app as api_app_module
    import bulletjournal.runtime as runtime_package
    import bulletjournal.runtime.artifacts as runtime_artifacts_module
    import bulletjournal.runtime.assets as runtime_assets_module
    import bulletjournal.runtime.context as runtime_context_module
    import bulletjournal.storage.graph_store as graph_store_module
    import bulletjournal.storage.object_store as object_store_module
    import bulletjournal.storage.project_fs as project_fs_module
    import bulletjournal.storage.state_db as state_db_module

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
    assert runtime_package.assets is runtime_assets_module
    assert runtime_package.get_node_id is runtime_context_module.get_node_id
    assert runtime_package.get_project_id is runtime_context_module.get_project_id

    with pytest.raises(AttributeError):
        bulletjournal.missing
    with pytest.raises(AttributeError):
        bulletjournal_api.missing
    with pytest.raises(AttributeError):
        bulletjournal_storage.missing


def test_runtime_artifacts_module_exposes_helper_functions_after_submodule_import() -> None:
    runtime_package = importlib.import_module('bulletjournal.runtime')
    runtime_module = importlib.import_module('bulletjournal.runtime.artifacts')

    imported = runtime_package.artifacts

    assert callable(runtime_module.pull)
    assert callable(runtime_module.pull_file)
    assert callable(runtime_module.push)
    assert callable(runtime_module.push_file)
    assert imported is runtime_module


def test_runtime_assets_module_exposes_helper_functions_after_submodule_import() -> None:
    runtime_package = importlib.import_module('bulletjournal.runtime')
    runtime_module = importlib.import_module('bulletjournal.runtime.assets')

    imported = runtime_package.assets

    assert callable(runtime_module.push)
    assert runtime_module.Collection is not None
    assert runtime_module.Iframe is not None
    assert runtime_module.Markdown is not None
    assert runtime_module.DataFrame is not None
    assert runtime_module.ScatterPlot is not None
    assert imported is runtime_module


def test_asset_packages_only_expose_canonical_asset_class_names() -> None:
    assets_module = importlib.import_module('bulletjournal.assets')
    asset_types_module = importlib.import_module('bulletjournal.assets.types')
    runtime_types_module = importlib.import_module('bulletjournal.assets.runtime_types')

    for module in (assets_module, asset_types_module, runtime_types_module):
        assert module.BarChart is not None
        assert module.Collection is not None
        assert module.DataFrame is not None
        assert module.Histogram is not None
        assert module.Iframe is not None
        assert module.Markdown is not None
        assert module.PieChart is not None
        assert module.ScatterPlot is not None

    for alias_name in (
        'BarChartAsset',
        'CollectionAsset',
        'DataFrameAsset',
        'HistogramAsset',
        'IframeAsset',
        'MarkdownAsset',
        'PieChartAsset',
        'ScatterPlotAsset',
    ):
        assert not hasattr(assets_module, alias_name)
        assert not hasattr(asset_types_module, alias_name)
        assert not hasattr(runtime_types_module, alias_name)


def test_runtime_helpers_return_active_context_ids(tmp_path: Path) -> None:
    import bulletjournal.runtime as runtime_package

    project_root = init_project_root(tmp_path / 'project').root
    context = runtime_context.RuntimeContext(
        project_root=project_root,
        node_id='sample_node',
        run_id='run-123',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
    )

    with runtime_context.activate_runtime_context(context):
        assert runtime_package.get_node_id() == 'sample_node'
        assert runtime_package.get_project_id() == 'project'


def test_template_registry_discovers_builtin_and_example_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package_dir = tmp_path / 'templates'
    builtin_dir = package_dir / 'builtin'
    example_notebook_dir = package_dir / 'examples' / 'notebooks'
    example_pipeline_dir = package_dir / 'examples' / 'pipelines'
    pycache_dir = builtin_dir / '__pycache__'
    builtin_dir.mkdir(parents=True)
    example_notebook_dir.mkdir(parents=True)
    example_pipeline_dir.mkdir(parents=True)
    pycache_dir.mkdir(parents=True)

    builtin_file = builtin_dir / 'example.py'
    skipped_file = pycache_dir / 'skip.py'
    example_file = example_notebook_dir / 'sample.py'
    pipeline_file = example_pipeline_dir / 'flow.json'
    for path in (builtin_file, skipped_file, example_file, pipeline_file):
        path.write_text('', encoding='utf-8')

    builtin_provider = FilesystemTemplateProvider(
        provider_name='builtin',
        notebook_root=builtin_dir,
        pipeline_root=package_dir / '_builtin_pipelines',
        origin_revision='builtin@0.1.0',
    )
    monkeypatch.setattr(template_registry, 'builtin_notebook_assets', builtin_provider.list_notebook_templates)
    monkeypatch.setattr(template_registry, 'builtin_pipeline_assets', lambda: [])
    monkeypatch.setattr(
        template_registry,
        'example_provider',
        lambda: FilesystemTemplateProvider(
            provider_name='examples',
            notebook_root=example_notebook_dir,
            pipeline_root=example_pipeline_dir,
            origin_revision='examples@0.1.0',
        ),
    )

    assert template_registry.builtin_templates() == [builtin_file]
    assert template_registry.builtin_pipeline_templates() == []
    assert template_registry.example_templates() == [example_file]
    assert template_registry.example_pipeline_templates() == [pipeline_file]


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
        'source_node': 'producer',
        'source_artifact': 'dataset',
        'loaded_version_id': 'version-1',
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
    runtime_artifacts.push(42, name='result', data_type=int, description='ignored')
    handle = runtime_artifacts.push_file(name='report', extension='.txt')

    assert value == 7
    assert file_path == '/tmp/data.csv'
    assert handle.name == 'report'
    assert handle.extension == '.txt'
    assert handle.role == ArtifactRole.OUTPUT
    assert calls == [
        ('validate', ('count', 'int')),
        ('resolve_pull', 'count'),
        ('record_pull', ('count', metadata)),
        ('resolve_pull_file', ('dataset', True)),
        ('record_pull', ('dataset', file_metadata)),
        ('push', ('result', 42, 'int', ArtifactRole.OUTPUT)),
    ]


def test_assets_api_delegates_to_runtime_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeContext:
        def finalize_asset_push(self, *, asset, name: str, title: str, description: str | None, asset_type) -> None:
            calls.append(('asset_push', (asset, name, title, description, asset_type)))

    monkeypatch.setattr(runtime_assets, 'current_runtime_context', lambda: FakeContext())

    asset = runtime_assets.Markdown('hello')
    runtime_assets.push(asset, name='summary', title='Summary', description='Notebook summary')

    assert calls == [('asset_push', (asset, 'summary', 'Summary', 'Notebook summary', None))]


def test_assets_api_rejects_invalid_artifact_name(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def finalize_asset_push(self, *, asset, name: str, title: str, description: str | None, asset_type) -> None:
            raise AssertionError('finalize_asset_push should not be called')

    monkeypatch.setattr(runtime_assets, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(
        ValueError,
        match=r'Invalid artifact name `bad-name`, must only contain lowercase letters, digits and underscores\.',
    ):
        runtime_assets.push(runtime_assets.Markdown('hello'), name='bad-name', title='Bad name')


def test_pie_chart_rejects_inconsistent_color_column() -> None:
    frame = pd.DataFrame(
        {
            'segment': ['a', 'a', 'b'],
            'segment_color': ['#ff0000', '#00ff00', '#0000ff'],
        }
    )

    with pytest.raises(ValueError, match='assigns multiple colors to category `a`'):
        runtime_assets.PieChart(frame, category='segment', color='segment_color')


@pytest.mark.parametrize(
    ('factory', 'kwargs'),
    [
        (runtime_assets.PieChart, {'category': 'segment', 'category_order': 'alphabetical'}),
        (runtime_assets.BarChart, {'category': 'segment', 'value': 'value', 'category_order': {'a': 1}}),
    ],
)
def test_chart_assets_reject_invalid_category_order(factory, kwargs: dict[str, object]) -> None:
    frame = pd.DataFrame(
        {
            'segment': ['a', 'b'],
            'value': [1, 2],
        }
    )

    with pytest.raises(TypeError, match='category_order'):
        factory(frame, **kwargs)


def test_collection_auto_names_children_and_rejects_nested_collections() -> None:
    collection = runtime_assets.Collection(display_mode='single')
    collection.add_asset(runtime_assets.Markdown('hello'))
    collection.add_asset(runtime_assets.Iframe('https://example.com/embed'), name='report', title='Embedded report')

    assert [entry.name for entry in collection._children] == ['asset_1', 'report']
    assert [entry.title for entry in collection._children] == ['Asset 1', 'Embedded report']

    with pytest.raises(TypeError, match='Collections cannot contain other collections'):
        collection.add_asset(runtime_assets.Collection())


def test_bar_chart_rejects_unsupported_aggregation() -> None:
    frame = pd.DataFrame(
        {
            'segment': ['a', 'b'],
            'value': [1, 2],
        }
    )

    with pytest.raises(ValueError, match='Bar chart `aggregation` must be one of'):
        runtime_assets.BarChart(frame, category='segment', value='value', aggregation='total')


def test_histogram_requires_bin_count_instead_of_bins() -> None:
    frame = pd.DataFrame({'value': [1, 2, 3]})

    with pytest.raises(TypeError, match='Use `bin_count` instead'):
        runtime_assets.Histogram(frame, x='value', bins=3)


def test_temporal_histograms_reject_unused_encoding_arguments() -> None:
    frame = pd.DataFrame({'created_at': pd.date_range('2024-01-01', periods=3, freq='D')})

    with pytest.raises(TypeError, match='do not support `shape` arguments'):
        runtime_assets.Histogram(frame, x='created_at', shape='created_at')


def test_artifacts_pull_file_returns_none_for_optional_missing_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        'path': None,
        'artifact_hash': 'missing',
        'state': 'ready',
        'warnings': [],
        'upstream_code_hash': 'default',
        'source_node': '',
        'source_artifact': '',
        'loaded_version_id': None,
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


def test_artifacts_push_rejects_none_without_optional_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def finalize_value_push(self, *, name: str, value, data_type: str, role: ArtifactRole) -> None:
            raise AssertionError('finalize_value_push should not be called')

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(TypeError, match=r'artifact.push\(\.\.\., value=None\) requires optional=True'):
        runtime_artifacts.push(None, name='result', data_type=int)


def test_artifacts_push_allows_none_for_optional_output(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeContext:
        def finalize_value_push(self, *, name: str, value, data_type: str, role: ArtifactRole) -> None:
            calls.append(('push', (name, value, data_type, role)))

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    runtime_artifacts.push(None, name='result', data_type=int, optional=True)

    assert calls == [('push', ('result', None, 'int', ArtifactRole.OUTPUT))]


def test_normalize_runtime_type_covers_known_and_fallback_types() -> None:
    GraphType = type('Graph', (), {'__module__': 'networkx.classes.graph'})

    assert runtime_artifacts._normalize_runtime_type('int') == 'int'
    assert runtime_artifacts._normalize_runtime_type(int) == 'int'
    assert runtime_artifacts._normalize_runtime_type(pd.DataFrame) == 'pandas.DataFrame'
    assert runtime_artifacts._normalize_runtime_type(pd.Series) == 'pandas.Series'
    assert runtime_artifacts._normalize_runtime_type(GraphType) == 'networkx.Graph'
    assert runtime_artifacts._normalize_runtime_type(object) == 'object'


def test_normalize_runtime_type_rejects_unsupported_runtime_values() -> None:
    fake_array = type('array', (), {'__module__': 'numpy'})

    assert runtime_artifacts._normalize_runtime_type(fake_array) == 'object'
    assert runtime_artifacts._normalize_runtime_type('mystery') == 'object'


def test_artifacts_pull_rejects_default_for_numpy_style_runtime_data_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def validate_pull_contract(self, *, name: str, data_type: str) -> None:
            raise AssertionError('validate_pull_contract should not be called when default type is invalid')

    fake_array = type('array', (), {'__module__': 'numpy'})

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(TypeError, match=r'Artifact default type mismatch: expected numpy\.array, got int\.'):
        runtime_artifacts.pull(name='test', data_type=fake_array, default=10)


def test_artifacts_pull_rejects_loaded_value_for_numpy_style_runtime_data_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {'value': 10, 'artifact_hash': 'abc', 'state': 'ready', 'warnings': [], 'upstream_code_hash': 'upstream'}
    calls: list[tuple[str, object]] = []

    class FakeContext:
        def validate_pull_contract(self, *, name: str, data_type: str) -> None:
            calls.append(('validate', (name, data_type)))

        def resolve_pull(self, name: str) -> dict[str, object]:
            calls.append(('resolve_pull', name))
            return metadata

        def record_pull(self, name: str, payload: dict[str, object]) -> None:
            calls.append(('record_pull', (name, payload)))

    fake_array = type('array', (), {'__module__': 'numpy'})

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(TypeError, match=r'Artifact import type mismatch: expected numpy\.array, got int\.'):
        runtime_artifacts.pull(name='test', data_type=fake_array)

    assert calls == [
        ('validate', ('test', 'object')),
        ('resolve_pull', 'test'),
    ]


def test_artifacts_push_rejects_numpy_style_runtime_data_type_value(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def finalize_value_push(self, *, name: str, value, data_type: str, role: ArtifactRole) -> None:
            raise AssertionError('finalize_value_push should not be called when export type is invalid')

    fake_array = type('array', (), {'__module__': 'numpy'})

    monkeypatch.setattr(runtime_artifacts, 'current_runtime_context', lambda: FakeContext())

    with pytest.raises(TypeError, match=r'Artifact export type mismatch: expected numpy\.array, got int\.'):
        runtime_artifacts.push(10, name='result', data_type=fake_array)


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
        with file_artifacts.FilePushHandle(name='report', role=ArtifactRole.OUTPUT, extension='.txt') as path:
            path.write_text('hello', encoding='utf-8')
            raise ValueError('boom')

    assert finalized == []
    assert temp_file.exists() is False


def test_artifacts_pull_file_returns_materialized_extension_path(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = runtime_context.RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-file-materialized-extension',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'incoming': runtime_context.Binding(
                source_node='producer',
                source_artifact='report',
                data_type='file',
            )
        },
        outputs={},
    )

    original_file = tmp_path / 'report-upload'
    original_file.write_text('payload', encoding='utf-8')
    persisted = context.object_store.persist_file(original_file, extension='.txt')
    context.db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='report',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code',
        upstream_data_hash='producer-data',
        run_id='producer-run',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    with runtime_context.activate_runtime_context(context):
        file_path = runtime_artifacts.pull_file(name='incoming')

    assert file_path is not None
    assert Path(file_path).parent == context.paths.pulled_files_dir
    assert Path(file_path).suffix == '.txt'
    assert Path(file_path).read_text(encoding='utf-8') == 'payload'


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


def test_install_script_runner_progress_hooks_tracks_scheduler_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marimo._runtime.app import script_runner as marimo_script_runner

    writes: list[dict[str, object]] = []

    class FakeScheduler:
        def __init__(self, cells: list[str]) -> None:
            self._cells_to_run = deque(cells)

        def pending(self) -> bool:
            return bool(self._cells_to_run)

        def pop_cell(self) -> str:
            return self._cells_to_run.popleft()

        @property
        def cells_to_run(self) -> deque[str]:
            return self._cells_to_run

    class FakeRunner:
        def __init__(self) -> None:
            self.app = SimpleNamespace(
                graph=SimpleNamespace(
                    cells={
                        'cell-1': SimpleNamespace(code='x = 1'),
                        'cell-2': SimpleNamespace(code='y = 2'),
                    }
                )
            )
            self._scheduler = FakeScheduler(['cell-1', 'cell-2'])

        def _run_synchronous(self, post_execute_hooks):
            _ = post_execute_hooks
            executed: list[str] = []
            while self._scheduler.pending():
                executed.append(self._scheduler.pop_cell())
            return executed

        async def _run_asynchronous(self, post_execute_hooks):
            _ = post_execute_hooks
            executed: list[str] = []
            while self._scheduler.pending():
                executed.append(self._scheduler.pop_cell())
            return executed

    monkeypatch.setattr(marimo_script_runner, 'AppScriptRunner', FakeRunner)
    monkeypatch.setattr(worker_main, '_write_progress', lambda path, payload: writes.append(payload))

    worker_main._install_script_runner_progress_hooks(
        notebook_path=tmp_path / 'sample_notebook.py',
        progress_path=tmp_path / 'progress.json',
    )

    sync_runner = FakeRunner()
    assert sync_runner._run_synchronous([]) == ['cell-1', 'cell-2']
    assert writes == [
        {'cell_id': 'cell-1', 'cell_number': 1, 'total_cells': 2, 'cell_code': 'x = 1'},
        {'cell_id': 'cell-2', 'cell_number': 2, 'total_cells': 2, 'cell_code': 'y = 2'},
    ]

    writes.clear()
    async_runner = FakeRunner()
    assert asyncio.run(async_runner._run_asynchronous([])) == ['cell-1', 'cell-2']
    assert writes == [
        {'cell_id': 'cell-1', 'cell_number': 1, 'total_cells': 2, 'cell_code': 'x = 1'},
        {'cell_id': 'cell-2', 'cell_number': 2, 'total_cells': 2, 'cell_code': 'y = 2'},
    ]


def test_install_script_runner_progress_hooks_requires_supported_scheduler_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marimo._runtime.app import script_runner as marimo_script_runner

    class FakeRunner:
        def _run_synchronous(self, post_execute_hooks):
            _ = post_execute_hooks
            return None

        async def _run_asynchronous(self, post_execute_hooks):
            _ = post_execute_hooks
            return None

    monkeypatch.setattr(marimo_script_runner, 'AppScriptRunner', FakeRunner)

    worker_main._install_script_runner_progress_hooks(
        notebook_path=tmp_path / 'sample_notebook.py',
        progress_path=tmp_path / 'progress.json',
    )

    with pytest.raises(RuntimeError, match='Unsupported marimo AppScriptRunner internals'):
        FakeRunner()._run_synchronous([])


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


def test_worker_runner_returns_structured_error_when_worker_result_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeProcess:
        returncode = 1

        def poll(self):
            return 1

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

    monkeypatch.setattr(runner_module.subprocess, 'Popen', lambda *args, **kwargs: FakeProcess())

    manifest = RunManifest(
        project_root=str(tmp_path),
        node_id='sample_node',
        notebook_path=str(tmp_path / 'sample_node.py'),
        run_id='run-123',
        source_hash='hash',
        lineage_mode=LineageMode.MANAGED.value,
        bindings={},
        outputs={},
    )

    result = runner_module.WorkerRunner().run(manifest, temp_dir=tmp_path)

    assert result['status'] == 'error'
    assert result['error'] == 'Worker exited with code 1 without producing a valid result file.'
    assert result['outputs'] == []
    assert result['stdout'] == ''
    assert result['stderr'] == ''
    assert result['returncode'] == 1


def test_worker_runner_respects_manifest_execution_log_paths(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    notebook_path.write_text(
        (
            'import marimo\n\n'
            'app = marimo.App()\n\n'
            '@app.cell\n'
            'def _():\n'
            "    print('hello from worker runner')\n"
            '    return\n\n'
            "if __name__ == '__main__':\n"
            '    app.run()\n'
        ),
        encoding='utf-8',
    )
    stdout_log = project_root / 'temp' / 'execution_logs' / 'run-123_sample_node.stdout.log'
    stderr_log = project_root / 'temp' / 'execution_logs' / 'run-123_sample_node.stderr.log'
    manifest = RunManifest(
        project_root=str(project_root),
        node_id='sample_node',
        notebook_path=str(notebook_path),
        run_id='run-123',
        source_hash='hash',
        lineage_mode=LineageMode.MANAGED.value,
        bindings={},
        outputs={},
        stdout_path=str(stdout_log),
        stderr_path=str(stderr_log),
    )

    result = runner_module.WorkerRunner().run(manifest, temp_dir=project_root / 'temp' / 'uploads')

    assert result['status'] == 'ok'
    assert result['stdout'] == 'hello from worker runner\n'
    assert stdout_log.read_text(encoding='utf-8') == 'hello from worker runner\n'
    assert stderr_log.read_text(encoding='utf-8') == ''


def test_worker_runner_completes_when_notebook_subprocess_writes_large_output(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'noisy_subprocess.py'
    notebook_path.write_text(
        (
            'import marimo\n\n'
            'app = marimo.App()\n\n'
            'with app.setup:\n'
            '    from bulletjournal.runtime import artifacts\n\n'
            '@app.cell\n'
            'def _():\n'
            '    import subprocess\n'
            '    import sys\n\n'
            '    _ = artifacts\n'
            "    code = \"import sys; sys.stdout.write('o' * 1_000_000); sys.stdout.flush(); sys.stderr.write('e' * 1_000_000); sys.stderr.flush()\"\n"
            '    subprocess.run([sys.executable, "-c", code], check=True)\n'
            '    return\n'
        ),
        encoding='utf-8',
    )
    stdout_log = project_root / 'temp' / 'execution_logs' / 'run-123_noisy_subprocess.stdout.log'
    stderr_log = project_root / 'temp' / 'execution_logs' / 'run-123_noisy_subprocess.stderr.log'
    manifest = RunManifest(
        project_root=str(project_root),
        node_id='noisy_subprocess',
        notebook_path=str(notebook_path),
        run_id='run-123',
        source_hash='hash',
        lineage_mode=LineageMode.MANAGED.value,
        bindings={},
        outputs={},
        stdout_path=str(stdout_log),
        stderr_path=str(stderr_log),
    )

    result = runner_module.WorkerRunner().run(manifest, temp_dir=project_root / 'temp' / 'worker')

    assert result['status'] == 'ok'
    assert len(result['stdout']) == 1_000_000
    assert len(result['stderr']) == 1_000_000
    assert stdout_log.read_text(encoding='utf-8') == result['stdout']
    assert stderr_log.read_text(encoding='utf-8') == result['stderr']


def test_worker_main_reports_setup_failures_in_result_file(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    manifest_path = tmp_path / 'manifest.json'
    result_path = tmp_path / 'result.json'
    manifest_path.write_text(
        json.dumps(
            {
                'project_root': str(project_root),
                'node_id': 'sample_node',
                'notebook_path': str(project_root / 'notebooks' / 'sample_node.py'),
                'run_id': 'run-123',
                'source_hash': 'hash',
                'lineage_mode': LineageMode.MANAGED.value,
                'bindings': {},
                'outputs': {},
                'progress_path': None,
                'result_path': str(result_path),
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(
        worker_main,
        '_install_script_runner_progress_hooks',
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('hook setup failed')),
    )

    exit_code = worker_main.main([str(manifest_path)])
    assert capsys.readouterr().out == ''
    payload = json.loads(result_path.read_text(encoding='utf-8'))

    assert exit_code == 1
    assert payload['status'] == 'error'
    assert payload['error'] == 'hook setup failed'
    assert payload['outputs'] == []
    assert 'RuntimeError: hook setup failed' in payload['traceback']


def test_worker_main_writes_notebook_output_to_inherited_streams_and_result_file(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    manifest_path = tmp_path / 'manifest.json'
    result_path = tmp_path / 'result.json'
    manifest_path.write_text(
        json.dumps(
            {
                'project_root': str(project_root),
                'node_id': 'sample_node',
                'notebook_path': str(project_root / 'notebooks' / 'sample_node.py'),
                'run_id': 'run-123',
                'source_hash': 'hash',
                'lineage_mode': LineageMode.MANAGED.value,
                'bindings': {},
                'outputs': {},
                'progress_path': None,
                'result_path': str(result_path),
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(worker_main, '_install_script_runner_progress_hooks', lambda **kwargs: None)

    def fake_execute_notebook(path: Path, *, progress_path: Path | None = None) -> dict[str, object]:
        _ = (path, progress_path)
        print('worker-side notebook output')
        return {'result': {'ok': True}}

    monkeypatch.setattr(worker_main, 'execute_notebook', fake_execute_notebook)

    exit_code = worker_main.main([str(manifest_path)])
    captured = capsys.readouterr()
    payload = json.loads(result_path.read_text(encoding='utf-8'))

    assert exit_code == 0
    assert payload['status'] == 'ok'
    assert payload['outputs'] == []
    assert captured.out == 'worker-side notebook output\n'


def test_worker_main_writes_result_file(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    manifest_path = tmp_path / 'manifest.json'
    result_path = tmp_path / 'result.json'
    manifest_path.write_text(
        json.dumps(
            {
                'project_root': str(project_root),
                'node_id': 'sample_node',
                'notebook_path': str(project_root / 'notebooks' / 'sample_node.py'),
                'run_id': 'run-123',
                'source_hash': 'hash',
                'lineage_mode': LineageMode.MANAGED.value,
                'bindings': {},
                'outputs': {},
                'progress_path': None,
                'result_path': str(result_path),
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(worker_main, '_install_script_runner_progress_hooks', lambda **kwargs: None)

    def fake_execute_notebook(path: Path, *, progress_path: Path | None = None) -> dict[str, object]:
        _ = (path, progress_path)
        print('worker-side stdout output')
        print('worker-side stderr output', file=sys.stderr)
        return {'result': {'ok': True}}

    monkeypatch.setattr(worker_main, 'execute_notebook', fake_execute_notebook)

    exit_code = worker_main.main([str(manifest_path)])
    captured = capsys.readouterr()
    payload = json.loads(result_path.read_text(encoding='utf-8'))

    assert exit_code == 0
    assert payload['status'] == 'ok'
    assert captured.out == 'worker-side stdout output\n'
    assert captured.err == 'worker-side stderr output\n'
