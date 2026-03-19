from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_archive import export_project_archive, import_project_archive
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.templates.registry import TemplateAsset


def test_project_archive_round_trip_preserves_project_id(tmp_path: Path) -> None:
    project_root = init_project_root(tmp_path / 'project', project_id='study-a').root
    archive_path = tmp_path / 'study-a.zip'

    exported = export_project_archive(project_root, archive_path, include_artifacts=False)
    imported = import_project_archive(archive_path, tmp_path / 'imported')

    assert exported['project_id'] == 'study-a'
    assert imported['project_id'] == 'study-a'
    assert (tmp_path / 'imported' / 'pyproject.toml').is_file()
    assert (tmp_path / 'imported' / 'uv.lock').is_file()


def test_template_service_discovers_external_provider(monkeypatch, tmp_path: Path) -> None:
    notebook_root = tmp_path / 'templates' / 'builtin'
    pipeline_root = tmp_path / 'templates' / 'pipelines'
    notebook_root.mkdir(parents=True)
    pipeline_root.mkdir(parents=True)
    (notebook_root / 'external_notebook.py').write_text('import marimo\napp = marimo.App()\n', encoding='utf-8')
    (pipeline_root / 'external_pipeline.json').write_text('{"nodes": [], "edges": [], "layout": []}', encoding='utf-8')

    provider = SimpleNamespace(
        notebook_templates=lambda: [
            TemplateAsset(
                provider='external',
                kind='notebook',
                name='external_notebook',
                file_name='external_notebook.py',
                ref='external/external_notebook',
                path=notebook_root / 'external_notebook.py',
                origin_revision='external@1.2.3',
            )
        ],
        pipeline_templates=lambda: [
            TemplateAsset(
                provider='external',
                kind='pipeline',
                name='external_pipeline',
                file_name='external_pipeline.json',
                ref='external/external_pipeline',
                path=pipeline_root / 'external_pipeline.json',
                origin_revision='external@1.2.3',
            )
        ],
    )

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])

    templates = TemplateService().list_templates()

    assert [template['ref'] for template in templates] == ['external/external_notebook', 'external/external_pipeline']
