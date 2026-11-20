from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import bulletjournal.templates.notebook_source as notebook_source
from bulletjournal.api.app import create_app
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.templates.provider import TemplateAsset
from bulletjournal.templates.validator import validate_pipeline_template_definition


@pytest.mark.parametrize(
    ('arguments', 'expected'),
    [
        ('app_title="LLM summary"', 'LLM summary'),
        ("app_title='[TikTok] Followers/Followings'", '[TikTok] Followers/Followings'),
        ('app_title="  URL analysis  "', 'URL analysis'),
        ('width="medium"', None),
        ('app_title="  "', None),
        ('app_title=None', None),
        ('app_title=42', None),
        ('app_title=some_function()', None),
        ('app_title=f"Title {value}"', None),
    ],
)
def test_extract_notebook_title_accepts_only_nonempty_string_literals(arguments, expected):
    assert notebook_source.extract_notebook_title(f'import marimo\napp = marimo.App({arguments})\n') == expected


def _provider(*, arguments='app_title="LLM summary"', title=None, override=False):
    source = f'import marimo\napp = marimo.App({arguments})\n'
    notebook = TemplateAsset(
        provider='test',
        kind='notebook',
        name='llm_summary',
        file_name='llm_summary.py',
        ref='test/llm_summary',
        path=None,
        origin_revision='test',
        title=title,
        source_loader=lambda: source,
    )
    node = {'id': 'different_node_id', 'kind': 'notebook', 'template_ref': notebook.ref}
    if override:
        node['title'] = 'Override'
    definition = {
        'nodes': [node],
        'edges': [],
        'layout': [{'node_id': node['id'], 'x': 0, 'y': 0, 'w': 320, 'h': 220}],
    }
    pipeline = TemplateAsset(
        provider='test',
        kind='pipeline',
        name='flow',
        file_name='flow.json',
        ref='test/flow',
        path=None,
        origin_revision='test',
        source_loader=lambda: json.dumps(definition),
    )
    return (
        SimpleNamespace(
            provider_name='test',
            list_notebook_templates=lambda: [notebook],
            list_pipeline_templates=lambda: [pipeline],
        ),
        notebook,
        definition,
    )


@pytest.mark.parametrize(
    ('arguments', 'asset_title', 'expected'),
    [
        ('app_title="LLM summary"', None, 'LLM summary'),
        ('width="medium"', None, 'Llm Summary'),
        ('app_title="LLM summary"', 'Provider title', 'Provider title'),
    ],
)
def test_catalog_uses_explicit_title_then_app_title_then_filename(monkeypatch, arguments, asset_title, expected):
    provider, _, _ = _provider(arguments=arguments, title=asset_title)
    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])
    service = TemplateService()
    assert service.get_template('test/llm_summary')['title'] == expected
    assert service.resolve_template_source('test/llm_summary').title == expected


@pytest.mark.parametrize('override', [False, True])
def test_pipeline_notebook_titles_are_forbidden(override):
    _, notebook, definition = _provider(override=override)
    issues = validate_pipeline_template_definition(
        definition,
        notebook_paths_by_ref={notebook.ref: notebook},
        node_id='flow',
    )
    if override:
        assert any(issue['code'] == 'pipeline_notebook_title_override' for issue in issues)
    else:
        assert issues == []


@pytest.mark.parametrize('title', ['', None, 123, 'Override'])
def test_pipeline_rejects_title_field_even_when_empty_or_nonstring(monkeypatch, title):
    provider, notebook, definition = _provider(override=True)
    definition['nodes'][0]['title'] = title
    issues = validate_pipeline_template_definition(
        definition,
        notebook_paths_by_ref={notebook.ref: notebook},
        node_id='flow',
    )
    assert any(issue['code'] == 'pipeline_notebook_title_override' for issue in issues)
    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])
    with pytest.raises(ValueError, match='must not define `title`'):
        TemplateService()


def test_pipeline_instantiation_uses_catalog_title_not_node_id(monkeypatch, tmp_path):
    provider, _, _ = _provider()
    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [provider])
    root = init_project_root(tmp_path / 'project').root
    with TestClient(create_app(project_path=root)) as client:
        snapshot = client.get('/api/v1/project/snapshot').json()
        response = client.patch(
            '/api/v1/graph',
            json={
                'graph_version': snapshot['graph']['meta']['graph_version'],
                'operations': [
                    {'type': 'add_pipeline_template', 'template_ref': 'test/flow', 'node_id_suffix': 'copy'}
                ],
            },
        )
        assert response.status_code == 200, response.text
        snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'different_node_id_copy')
    assert node['title'] == 'LLM summary'
    assert next(item for item in snapshot['templates'] if item['ref'] == 'test/llm_summary')['title'] == node['title']
