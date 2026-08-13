from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bulletjournal.storage.project_archive as project_archive_module
from bulletjournal.api.app import create_app
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_archive import ProjectExportMode, export_project_archive, import_project_archive
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root
from bulletjournal.storage.state_db import StateDB
from bulletjournal.templates.builtin_provider import FilesystemTemplateProvider, example_provider


def test_project_archive_round_trip_preserves_project_id(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'

    exported = export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)
    imported = import_project_archive(archive_path, tmp_path / 'imported')

    assert exported['project_id'] == 'study-a'
    assert imported['project_id'] == 'study-a'
    assert exported['mode'] == ProjectExportMode.CODE_ONLY.value
    assert (tmp_path / 'imported' / 'pyproject.toml').is_file()
    assert (tmp_path / 'imported' / 'uv.lock').is_file()


@pytest.mark.parametrize('mode', list(ProjectExportMode))
def test_project_archive_modes_round_trip_final_state_schema(tmp_path: Path, mode: ProjectExportMode) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute('ALTER TABLE objects ADD COLUMN nondeterministic INTEGER NOT NULL DEFAULT 0')
        connection.execute(
            'CREATE TABLE cache_index ('
            'node_id TEXT NOT NULL, artifact_name TEXT NOT NULL, upstream_data_hash TEXT NOT NULL, '
            'artifact_hash TEXT NOT NULL, is_nondeterministic INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, '
            'PRIMARY KEY (node_id, artifact_name, upstream_data_hash))'
        )
        connection.execute("DELETE FROM schema_migrations WHERE name = '007_remove_cache_and_nondeterminism'")
    archive_path = tmp_path / f'{mode.value}.zip'

    export_project_archive(project_root, archive_path, mode=mode)
    imported_root = tmp_path / 'imported'
    import_project_archive(archive_path, imported_root)

    with sqlite3.connect(imported_root / 'metadata' / 'state.db') as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {row[1] for row in connection.execute('PRAGMA table_info(objects)')}
    assert 'cache_index' not in tables
    assert 'nondeterministic' not in columns


def test_project_archive_import_migration_failure_cleans_staging_and_destination(tmp_path: Path, monkeypatch) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    export_project_archive(project_root, archive_path, mode=ProjectExportMode.FULL)
    destination = tmp_path / 'imported'

    def fail_migration(path: Path) -> None:
        raise RuntimeError(f'migration failed for {path}')

    monkeypatch.setattr(project_archive_module, 'StateDB', fail_migration)

    with pytest.raises(RuntimeError, match='migration failed'):
        import_project_archive(archive_path, destination)

    assert not destination.exists()
    assert not (tmp_path / '.imported.import.tmp').exists()


def test_project_archive_round_trip_preserves_dashboards(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    dashboard_path = project_root / 'dashboards' / 'study_a.json'
    dashboard_path.write_text(
        json.dumps(
            {
                'schema_version': 1,
                'dashboard_id': 'study_a',
                'version': 1,
                'title': 'Study Dashboard',
                'created_at': '2026-01-01T00:00:00Z',
                'updated_at': '2026-01-01T00:00:00Z',
                'sources': [],
                'panels': [],
            }
        ),
        encoding='utf-8',
    )
    archive_path = tmp_path / 'study-a.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)
    import_project_archive(archive_path, tmp_path / 'imported')

    imported_dashboard = tmp_path / 'imported' / 'dashboards' / 'study_a.json'
    assert imported_dashboard.is_file()
    assert json.loads(imported_dashboard.read_text(encoding='utf-8'))['title'] == 'Study Dashboard'


def test_project_archive_import_rejects_schema_version_1(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    invalid_archive_path = tmp_path / 'study-a-invalid.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)

    with zipfile.ZipFile(archive_path) as source_zip, zipfile.ZipFile(invalid_archive_path, 'w') as target_zip:
        for info in source_zip.infolist():
            payload = source_zip.read(info.filename)
            if info.filename == 'metadata/project.json':
                project_json = json.loads(payload.decode('utf-8'))
                project_json['schema_version'] = 1
                payload = json.dumps(project_json).encode('utf-8')
            target_zip.writestr(info, payload)

    with pytest.raises(ProjectValidationError, match='Schema version 1 projects are no longer supported'):
        import_project_archive(invalid_archive_path, tmp_path / 'imported-invalid')


def test_project_archive_full_export_falls_back_for_incompatible_project_schema(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))
    project_json['schema_version'] = 1
    paths.project_json_path.write_text(json.dumps(project_json), encoding='utf-8')
    paths.uv_lock_path.unlink()
    paths.checkpoints_dir.rmdir()
    pulled_file = paths.pulled_files_dir / 'raw.csv'
    pulled_file.parent.mkdir(parents=True, exist_ok=True)
    pulled_file.write_text('a,b\n1,2\n', encoding='utf-8')
    archive_path = tmp_path / 'study-a-full.zip'

    exported = export_project_archive(project_root, archive_path, mode=ProjectExportMode.FULL)

    assert exported['project_id'] == 'study-a'
    assert exported['mode'] == ProjectExportMode.FULL.value
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
        assert 'temp/pulled_files/raw.csv' in names
        assert 'uv.lock' not in names
        assert 'checkpoints/' not in names
        archived_project_json = json.loads(zf.read('metadata/project.json').decode('utf-8'))
    assert archived_project_json['schema_version'] == 1


@pytest.mark.parametrize('mode', [ProjectExportMode.CODE_ONLY, ProjectExportMode.CODE_AND_DATA])
def test_project_archive_non_full_exports_reject_incompatible_project_schema(
    tmp_path: Path,
    mode: ProjectExportMode,
) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    project_json = json.loads(paths.project_json_path.read_text(encoding='utf-8'))
    project_json['schema_version'] = 1
    paths.project_json_path.write_text(json.dumps(project_json), encoding='utf-8')
    archive_path = tmp_path / f'{mode.value}.zip'

    with pytest.raises(ProjectValidationError, match='Schema version 1 projects are no longer supported'):
        export_project_archive(project_root, archive_path, mode=mode)


def test_project_archive_export_uses_root_level_layout_without_manifest(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)

    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())

    assert 'graph/meta.json' in names
    assert 'metadata/project.json' in names
    assert 'metadata/state.db' in names
    assert 'project/graph/meta.json' not in names
    assert 'export_manifest.json' not in names
    assert 'objects/' in names
    assert 'checkpoints/' in names


def test_project_archive_export_modes_include_expected_payloads(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    object_file = paths.object_store_dir / 'ab' / 'cdef'
    object_file.parent.mkdir(parents=True, exist_ok=True)
    object_file.write_text('artifact', encoding='utf-8')
    checkpoint_dir = paths.checkpoints_dir / 'cp-1'
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / 'graph').mkdir()
    (checkpoint_dir / 'notebooks').mkdir()

    archives = {mode: tmp_path / f'{mode.value}.zip' for mode in ProjectExportMode}
    for mode, archive_path in archives.items():
        export_project_archive(project_root, archive_path, mode=mode)

    with zipfile.ZipFile(archives[ProjectExportMode.CODE_ONLY]) as zf:
        names = set(zf.namelist())
        assert 'objects/ab/cdef' not in names
        assert 'checkpoints/cp-1/graph/' not in names
    with zipfile.ZipFile(archives[ProjectExportMode.CODE_AND_DATA]) as zf:
        names = set(zf.namelist())
        assert 'objects/ab/cdef' in names
        assert 'checkpoints/cp-1/graph/' not in names
    with zipfile.ZipFile(archives[ProjectExportMode.FULL]) as zf:
        names = set(zf.namelist())
        assert 'objects/ab/cdef' in names
        assert 'checkpoints/cp-1/graph/' in names


def test_project_archive_code_only_reconciles_state_db(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    db = StateDB(paths.state_db_path)
    paths.execution_logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.execution_logs_dir / 'run-1_node-a.stdout.log').write_text('stdout', encoding='utf-8')
    (paths.execution_logs_dir / 'run-1_node-a.stderr.log').write_text('stderr', encoding='utf-8')

    db.upsert_artifact_object(
        'hash-1', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '1', 'truncated': False}
    )
    db.pin_object('publication', 'publication-1', 'hash-1')
    db.acquire_object_lease('hash-1', 'download', 'request-1', expires_at='2099-01-01T00:00:00Z')
    db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-1',
        source_hash='source-a',
        upstream_code_hash='code-a',
        upstream_data_hash='data-a',
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    db.replace_asset_declarations(
        'node-a',
        'source-a',
        [],
    )
    asset_version_id = db.create_asset_version(
        node_id='node-a',
        asset_name='asset',
        asset_type='markdown',
        interactive=False,
        source_hash='source-a',
        upstream_code_hash='code-a',
        upstream_data_hash='data-a',
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        definition={'kind': 'markdown', 'content': 'hello'},
        modifier_schema=[],
        default_modifiers={},
        override_schema_hash='schema-hash',
        warnings=[],
        objects=[{'object_role': 'primary', 'artifact_hash': 'hash-1'}],
        state=ArtifactState.READY,
    )
    assert asset_version_id > 0
    db.record_run('run-1', 'study-a', 'run_stale', {'node_id': 'node-a'}, 1, {'started_at': '2026-01-01T00:00:00Z'})
    db.record_run_input('run-1', 'node-a/output', 'hash-1', ArtifactState.READY.value)
    db.upsert_notebook_execution_head(
        node_id='node-a',
        state=ArtifactState.READY,
        source_hash='source-a',
        upstream_code_hash='code-a',
        upstream_data_hash='data-a',
        run_id='run-1',
        last_run_started_at='2026-01-01T00:00:00Z',
        last_run_finished_at='2026-01-01T00:00:05Z',
    )
    db.upsert_orchestrator_execution_meta(
        node_id='node-a',
        run_id='run-1',
        status='running',
        started_at='2026-01-01T00:00:00Z',
        stdout_path=str(paths.execution_logs_dir / 'run-1_node-a.stdout.log'),
        stderr_path=str(paths.execution_logs_dir / 'run-1_node-a.stderr.log'),
    )
    db.create_checkpoint('cp-1', 1, str(paths.checkpoints_dir / 'cp-1'))
    archive_path = tmp_path / 'code-only.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)
    import_root = tmp_path / 'imported'
    import_project_archive(archive_path, import_root)
    imported_db = StateDB(import_root / 'metadata' / 'state.db')
    artifact_head = imported_db.get_artifact_head('node-a', 'output')
    asset_head = imported_db.get_asset_head('node-a', 'asset')
    execution_head = imported_db.get_notebook_execution_head('node-a')

    assert artifact_head is not None
    assert artifact_head['current_version_id'] is None
    assert artifact_head['state'] == ArtifactState.PENDING.value
    assert asset_head is not None
    assert asset_head['current_asset_version_id'] is None
    assert asset_head['state'] == ArtifactState.PENDING.value
    assert execution_head is not None
    assert execution_head['state'] == ArtifactState.PENDING.value
    assert imported_db.list_run_records() == []
    assert imported_db.list_checkpoints() == []
    assert imported_db.list_orchestrator_execution_meta() == {}
    with imported_db._connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM object_pins').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM object_leases').fetchone()[0] == 0
    assert (import_root / 'objects').exists()
    assert not any((import_root / 'objects').rglob('*'))


def test_project_archive_code_and_data_removes_checkpoint_rows(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    paths = ProjectPaths(project_root)
    db = StateDB(paths.state_db_path)
    checkpoint_dir = paths.checkpoints_dir / 'cp-1'
    checkpoint_dir.mkdir(parents=True)
    db.create_checkpoint('cp-1', 1, str(checkpoint_dir))
    archive_path = tmp_path / 'code-and-data.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_AND_DATA)
    import_root = tmp_path / 'imported'
    import_project_archive(archive_path, import_root)
    imported_db = StateDB(import_root / 'metadata' / 'state.db')

    assert imported_db.list_checkpoints() == []


def test_project_archive_import_rejects_nested_project_root(tmp_path: Path) -> None:
    archive_path = tmp_path / 'nested.zip'
    with zipfile.ZipFile(archive_path, 'w') as zf:
        zf.writestr('project/metadata/project.json', '{}')
        zf.writestr('project/metadata/state.db', b'')
        zf.writestr('project/graph/meta.json', '{}')
        zf.writestr('project/graph/nodes.json', '[]')
        zf.writestr('project/graph/edges.json', '[]')
        zf.writestr('project/graph/layout.json', '[]')
        zf.writestr('project/pyproject.toml', '[project]\nname = "demo"\n')
        zf.writestr('project/uv.lock', 'version = 1\n')

    with pytest.raises(ProjectValidationError, match='rooted at zip root'):
        import_project_archive(archive_path, tmp_path / 'imported')


def test_project_archive_import_rejects_manifest_file(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    invalid_archive_path = tmp_path / 'study-a-invalid.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)

    with zipfile.ZipFile(archive_path) as source_zip, zipfile.ZipFile(invalid_archive_path, 'w') as target_zip:
        for info in source_zip.infolist():
            target_zip.writestr(info, source_zip.read(info.filename))
        target_zip.writestr('export_manifest.json', '{}')

    with pytest.raises(ProjectValidationError, match=r'must not contain `export_manifest\.json`'):
        import_project_archive(invalid_archive_path, tmp_path / 'imported-invalid')


def test_project_archive_import_rejects_invalid_graph_metadata(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    invalid_archive_path = tmp_path / 'study-a-invalid.zip'

    export_project_archive(project_root, archive_path, mode=ProjectExportMode.CODE_ONLY)

    with zipfile.ZipFile(archive_path) as source_zip, zipfile.ZipFile(invalid_archive_path, 'w') as target_zip:
        for info in source_zip.infolist():
            payload = source_zip.read(info.filename)
            if info.filename == 'graph/meta.json':
                graph_meta = json.loads(payload.decode('utf-8'))
                graph_meta['project_id'] = 'other-project'
                payload = json.dumps(graph_meta).encode('utf-8')
            target_zip.writestr(info, payload)

    with pytest.raises(ProjectValidationError, match='Archive graph metadata has project_id'):
        import_project_archive(invalid_archive_path, tmp_path / 'imported-invalid')


def test_template_service_discovers_external_provider(monkeypatch, tmp_path: Path) -> None:
    notebook_source = 'import marimo\napp = marimo.App()\n'
    pipeline_source = '{"title": "External Pipeline", "nodes": [], "edges": [], "layout": []}\n'
    notebook_documentation = 'External notebook docs.\n\n- First step\n- Second step'

    provider = SimpleNamespace(
        list_notebook_templates=lambda: [
            {
                'name': 'external_notebook',
                'ref': 'external/external_notebook',
                'title': 'External Notebook',
                'documentation': notebook_documentation,
                'path': 'notebooks/external_notebook.py',
                'hidden': False,
            }
        ],
        list_pipeline_templates=lambda: [
            {
                'name': 'external_pipeline',
                'ref': 'external/external_pipeline',
                'title': 'External Pipeline',
                'documentation': 'External pipeline docs.',
                'path': 'pipelines/external_pipeline.json',
                'hidden': False,
            }
        ],
        provider_name='external',
        provider_revision='external@1.2.3',
        load_notebook_template=lambda name: notebook_source if name == 'external_notebook' else '',
        load_pipeline_template=lambda name: pipeline_source if name == 'external_pipeline' else '',
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    templates = TemplateService().list_templates()

    assert [template['ref'] for template in templates] == ['external/external_notebook', 'external/external_pipeline']
    templates_by_ref = {template['ref']: template for template in templates}
    assert templates_by_ref['external/external_notebook']['title'] == 'External Notebook'
    assert templates_by_ref['external/external_notebook']['documentation'] == notebook_documentation
    assert templates_by_ref['external/external_pipeline']['title'] == 'External Pipeline'
    assert templates_by_ref['external/external_pipeline']['documentation'] == 'External pipeline docs.'


def test_template_service_marks_hidden_notebooks_but_keeps_pipelines_visible(monkeypatch, tmp_path: Path) -> None:
    notebook_root = tmp_path / 'templates' / 'builtin'
    pipeline_root = tmp_path / 'templates' / 'pipelines'
    notebook_root.mkdir(parents=True)
    pipeline_root.mkdir(parents=True)
    (notebook_root / 'hidden_notebook.py').write_text(
        "import marimo\n\napp = marimo.App(width='medium', app_title='Hidden Notebook')\n",
        encoding='utf-8',
    )
    (pipeline_root / 'hidden_pipeline.json').write_text(
        '{"title": "Hidden Pipeline", "nodes": [{"id": "hidden_node", "title": "Hidden Node", "kind": "notebook", "template_ref": "external/hidden_notebook"}], "edges": [], "layout": [{"node_id": "hidden_node", "x": 0, "y": 0, "w": 320, "h": 200}]}',
        encoding='utf-8',
    )

    provider = FilesystemTemplateProvider(
        provider_name='external',
        notebook_root=notebook_root,
        pipeline_root=pipeline_root,
        origin_revision='external@1.2.3',
    )

    hidden_notebook_asset = provider.list_notebook_templates()[0]
    hidden_pipeline_asset = provider.pipeline_templates()[0]

    provider_with_hidden = SimpleNamespace(
        list_notebook_templates=lambda: [replace(hidden_notebook_asset, hidden=True)],
        pipeline_templates=lambda: [hidden_pipeline_asset],
    )

    monkeypatch.setattr(
        'bulletjournal.services.template_service.discover_template_providers', lambda: [provider_with_hidden]
    )

    templates = TemplateService().list_templates()
    templates_by_ref = {template['ref']: template for template in templates}

    assert templates_by_ref['external/hidden_notebook']['hidden'] is True
    assert templates_by_ref['external/hidden_pipeline']['hidden'] is False


def test_template_service_lists_examples_but_not_builtin_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'bulletjournal.services.template_service.discover_template_providers',
        lambda: [example_provider()],
    )

    templates = TemplateService().list_templates()
    refs = {template['ref'] for template in templates}

    assert 'examples/movie_dataset_download' in refs
    assert 'examples/example_movie_pipeline' in refs
    assert not any(ref.startswith('builtin/') for ref in refs)


def test_example_templates_use_notebook_markdown_as_documentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'bulletjournal.services.template_service.discover_template_providers',
        lambda: [example_provider()],
    )

    templates = {template['ref']: template for template in TemplateService().list_templates()}

    assert templates['examples/movie_dataset_download']['documentation'].startswith('# Download movie dataset')
    assert 'downloads a CSV file from the provided URL' in templates['examples/movie_dataset_download']['documentation']
    assert templates['examples/movie_recommendation']['documentation'].startswith('# Movie recommendation')
    assert 'high-signal recommendations' in templates['examples/movie_recommendation']['documentation']


def test_template_service_supports_legacy_example_aliases_when_examples_are_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'bulletjournal.services.template_service.discover_template_providers',
        lambda: [example_provider()],
    )
    service = TemplateService()

    notebook = service.resolve_template_source('builtin/example_1', allow_inactive=False)
    pipeline = service.resolve_pipeline_template('builtin/example_iris_pipeline', allow_inactive=False)

    assert notebook.ref == 'examples/movie_dataset_download'
    assert service.template_ref('builtin/example_1').ref == 'examples/movie_dataset_download'
    assert pipeline.ref == 'examples/example_movie_pipeline'


def test_template_service_hides_examples_when_external_provider_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    external_provider = SimpleNamespace(
        provider_name='external',
        provider_revision='external@1.0.0',
        list_notebook_templates=lambda: [
            {
                'name': 'external_notebook',
                'ref': 'external/external_notebook',
                'title': 'External Notebook',
                'documentation': 'External notebook docs.',
                'path': 'notebooks/external_notebook.py',
                'hidden': False,
            }
        ],
        list_pipeline_templates=lambda: [],
        load_notebook_template=lambda name: 'import marimo\napp = marimo.App()\n',
        load_pipeline_template=lambda name: '{}',
    )
    monkeypatch.setattr(
        'bulletjournal.services.template_service.discover_template_providers',
        lambda: [example_provider(), external_provider],
    )
    service = TemplateService()

    refs = {template['ref'] for template in service.list_templates()}

    assert refs == {'external/external_notebook'}
    with pytest.raises(FileNotFoundError):
        service.resolve_template_source('builtin/example_1', allow_inactive=False)
    with pytest.raises(FileNotFoundError):
        service.resolve_pipeline_template('builtin/example_iris_pipeline', allow_inactive=False)


def test_template_service_supports_provider_loaders_without_files(monkeypatch) -> None:
    notebook_source = 'import marimo\napp = marimo.App()\n'
    pipeline_source = '{"title": "Hidden Pipeline", "nodes": [], "edges": [], "layout": []}\n'
    notebook_documentation = 'Helper notebook docs.'

    provider = SimpleNamespace(
        provider_name='agoratlas',
        provider_revision='0.1.0+abc123',
        list_notebook_templates=lambda: [
            {
                'name': 'private/helper',
                'ref': 'agoratlas/private/helper',
                'title': 'Helper',
                'documentation': notebook_documentation,
                'path': 'notebooks/private/_helper.py',
                'hidden': True,
            }
        ],
        list_pipeline_templates=lambda: [
            {
                'name': 'iris_pipeline',
                'ref': 'agoratlas/iris_pipeline',
                'title': 'Iris Pipeline',
                'documentation': 'Pipeline docs.',
                'path': 'pipelines/iris_pipeline.json',
                'hidden': False,
            }
        ],
        load_notebook_template=lambda name: notebook_source if name == 'private/helper' else '',
        load_pipeline_template=lambda name: pipeline_source if name == 'iris_pipeline' else '',
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    service = TemplateService()

    notebook = service.resolve_template_source('agoratlas/private/helper')
    pipeline = service.resolve_pipeline_template('agoratlas/iris_pipeline')
    listed = {template['ref']: template for template in service.list_templates()}

    assert notebook.source_text == notebook_source
    assert notebook.documentation == notebook_documentation
    assert notebook.origin_revision == '0.1.0+abc123'
    assert pipeline.source_text == pipeline_source
    assert pipeline.documentation == 'Pipeline docs.'
    assert listed['agoratlas/private/helper']['hidden'] is True
    assert listed['agoratlas/private/helper']['documentation'] == notebook_documentation
    assert listed['agoratlas/iris_pipeline']['title'] == 'Iris Pipeline'
    assert listed['agoratlas/iris_pipeline']['documentation'] == 'Pipeline docs.'


def test_template_service_caches_notebook_metadata_and_template_list(monkeypatch: pytest.MonkeyPatch) -> None:
    notebook_source = "import marimo\n\napp = marimo.App(app_title='Template')\n"
    load_count = 0

    def load_notebook_template(name: str) -> str:
        nonlocal load_count
        assert name == 'cached_notebook'
        load_count += 1
        return notebook_source

    provider = SimpleNamespace(
        provider_name='cached',
        provider_revision='cached@1.0.0',
        list_notebook_templates=lambda: [{'name': 'cached_notebook', 'ref': 'cached/cached_notebook'}],
        list_pipeline_templates=lambda: [],
        load_notebook_template=load_notebook_template,
        load_pipeline_template=lambda name: '',
    )
    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    service = TemplateService()
    initial_load_count = load_count

    first = service.list_templates()
    second = service.list_templates()
    resolved = service.resolve_template_source('cached/cached_notebook')
    rendered = service.render_resolved_notebook_template_source('cached/cached_notebook', node_id='node')

    assert initial_load_count > 0
    assert load_count == initial_load_count
    assert first == second
    assert first is not second
    assert resolved.source_text == notebook_source
    assert "app_title='node'" in rendered


def test_template_service_rewrites_notebook_app_title_from_node_id() -> None:
    rendered = TemplateService.render_notebook_template_source(
        "app = marimo.App(width='medium', app_title='Visible Title')\n",
        node_id='sample_node',
    )

    assert rendered == "app = marimo.App(width='medium', app_title='sample_node')\n"


def test_template_service_preserves_other_marimo_app_arguments() -> None:
    rendered = TemplateService.render_notebook_template_source(
        "app = marimo.App(width='medium', layout_file='layout.json', app_title='Visible Title')  # keep\n",
        node_id='sample_node',
    )

    assert rendered == "app = marimo.App(width='medium', layout_file='layout.json', app_title='sample_node')  # keep\n"


def test_template_service_rewrites_multiline_provider_notebook_app_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(
        provider_name='acme',
        provider_revision='0.1.0',
        list_notebook_templates=lambda: [
            {
                'name': 'broken_notebook',
                'ref': 'acme/broken_notebook',
                'title': 'Broken Notebook',
                'path': 'notebooks/broken_notebook.py',
                'hidden': False,
            }
        ],
        list_pipeline_templates=lambda: [],
        load_notebook_template=lambda name: (
            "import marimo\n\napp = marimo.App(\n    width='medium',\n    app_title='Broken Notebook',\n)\n"
            if name == 'broken_notebook'
            else ''
        ),
        load_pipeline_template=lambda name: '',
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    service = TemplateService()

    rendered = service.render_resolved_notebook_template_source('acme/broken_notebook', node_id='renamed_notebook')

    assert rendered == "import marimo\n\napp = marimo.App(width='medium', app_title='renamed_notebook')\n"


def test_template_service_rejects_invalid_provider_notebook_artifact_names(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(
        provider_name='acme',
        provider_revision='0.1.0',
        list_notebook_templates=lambda: [
            {
                'name': 'broken_notebook',
                'ref': 'acme/broken_notebook',
                'title': 'Broken Notebook',
                'path': 'notebooks/broken_notebook.py',
                'hidden': False,
            }
        ],
        list_pipeline_templates=lambda: [],
        load_notebook_template=lambda name: (
            "import marimo\n\napp = marimo.App()\n\nwith app.setup:\n    from bulletjournal.runtime import artifacts\n\n@app.cell\ndef _():\n    artifacts.push(1, name='bad-name', data_type=int)\n    return\n"
            if name == 'broken_notebook'
            else ''
        ),
        load_pipeline_template=lambda name: '',
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    with pytest.raises(ValueError, match='Invalid artifact name `bad-name`'):
        TemplateService()


def test_template_service_rejects_invalid_provider_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(
        provider_name='acme',
        provider_revision='0.1.0',
        list_notebook_templates=lambda: [],
        list_pipeline_templates=lambda: [
            {
                'name': 'broken_pipeline',
                'ref': 'acme/broken_pipeline',
                'title': 'Broken Pipeline',
                'path': 'pipelines/broken_pipeline.json',
                'hidden': False,
            }
        ],
        load_notebook_template=lambda name: '',
        load_pipeline_template=lambda name: (
            json.dumps(
                {
                    'title': 'Broken Pipeline',
                    'nodes': [
                        {'id': 'source', 'title': 'Source', 'kind': 'constant', 'data_type': 'int'},
                    ],
                    'edges': [],
                    'layout': [{'node_id': 'source', 'x': 0, 'y': 0, 'w': 100, 'h': 40}],
                }
            )
            if name == 'broken_pipeline'
            else ''
        ),
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    with pytest.raises(ValueError, match='Invalid pipeline template `acme/broken_pipeline`'):
        TemplateService()


def test_create_app_fails_fast_for_invalid_provider_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(
        provider_name='acme',
        provider_revision='0.1.0',
        list_notebook_templates=lambda: [],
        list_pipeline_templates=lambda: [
            {
                'name': 'broken_pipeline',
                'ref': 'acme/broken_pipeline',
                'title': 'Broken Pipeline',
                'path': 'pipelines/broken_pipeline.json',
                'hidden': False,
            }
        ],
        load_notebook_template=lambda name: '',
        load_pipeline_template=lambda name: (
            json.dumps(
                {
                    'title': 'Broken Pipeline',
                    'nodes': [
                        {'id': 'source', 'title': 'Source', 'kind': 'constant', 'data_type': 'int'},
                    ],
                    'edges': [],
                    'layout': [{'node_id': 'source', 'x': 0, 'y': 0, 'w': 100, 'h': 40}],
                }
            )
            if name == 'broken_pipeline'
            else ''
        ),
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    with pytest.raises(ValueError, match='Invalid pipeline template `acme/broken_pipeline`'):
        create_app()
