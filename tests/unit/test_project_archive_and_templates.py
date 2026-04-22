from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bulletjournal.storage.project_archive as project_archive_module
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_archive import export_project_archive, import_project_archive
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.templates.builtin_provider import FilesystemTemplateProvider, example_provider


def test_project_archive_round_trip_preserves_project_id(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'

    exported = export_project_archive(project_root, archive_path, include_artifacts=False)
    imported = import_project_archive(archive_path, tmp_path / 'imported')

    assert exported['project_id'] == 'study-a'
    assert imported['project_id'] == 'study-a'
    assert (tmp_path / 'imported' / 'pyproject.toml').is_file()
    assert (tmp_path / 'imported' / 'uv.lock').is_file()


def test_project_archive_export_ignores_missing_sqlite_sidecars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    original_write = zipfile.ZipFile.write

    monkeypatch.setattr(
        project_archive_module,
        '_iter_project_files',
        lambda paths, *, include_artifacts: [Path('metadata/state.db'), Path('metadata/state.db-shm')],
    )

    def flaky_write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        if Path(filename).name == 'state.db-shm':
            raise FileNotFoundError(filename)
        return original_write(
            self,
            filename,
            arcname=arcname,
            compress_type=compress_type,
            compresslevel=compresslevel,
        )

    monkeypatch.setattr(zipfile.ZipFile, 'write', flaky_write)

    exported = export_project_archive(project_root, archive_path, include_artifacts=False)

    assert exported['project_id'] == 'study-a'
    with zipfile.ZipFile(archive_path) as zf:
        assert 'project/metadata/state.db' in zf.namelist()
        assert 'project/metadata/state.db-shm' not in zf.namelist()


def test_project_archive_export_reraises_missing_non_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'
    original_write = zipfile.ZipFile.write

    monkeypatch.setattr(
        project_archive_module,
        '_iter_project_files',
        lambda paths, *, include_artifacts: [Path('metadata/state.db'), Path('metadata/project.json')],
    )

    def flaky_write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        if Path(filename).name == 'project.json':
            raise FileNotFoundError(filename)
        return original_write(
            self,
            filename,
            arcname=arcname,
            compress_type=compress_type,
            compresslevel=compresslevel,
        )

    monkeypatch.setattr(zipfile.ZipFile, 'write', flaky_write)

    with pytest.raises(FileNotFoundError):
        export_project_archive(project_root, archive_path, include_artifacts=False)


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

    assert 'examples/example_1' in refs
    assert 'examples/example_iris_pipeline' in refs
    assert not any(ref.startswith('builtin/') for ref in refs)


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

    assert notebook.ref == 'examples/example_1'
    assert service.template_ref('builtin/example_1').ref == 'examples/example_1'
    assert pipeline.ref == 'examples/example_iris_pipeline'


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


def test_template_service_renders_notebook_template_placeholders() -> None:
    rendered = TemplateService.render_notebook_template_source(
        "app = marimo.App(width='medium', app_title='{{NODE_ID}} / {{TITLE}}')\n",
        title='Sample Node',
        node_id='sample_node',
    )

    assert rendered == "app = marimo.App(width='medium', app_title='sample_node / Sample Node')\n"
