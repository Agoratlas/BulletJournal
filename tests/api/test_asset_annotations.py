from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.domain.enums import LineageMode
from bulletjournal.domain.models import AssetDeclaration
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB


@pytest.fixture
def asset_project(tmp_path):
    root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=root)
    client = TestClient(app)
    version = client.get('/api/v1/graph').json()['meta']['graph_version']
    response = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'producer',
                    'title': 'Producer',
                    'source_text': """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('first'), name='first', title='first')
    assets.push(assets.Markdown('second'), name='second', title='second')
    assets.push(assets.Collection(), name='collection', title='collection', asset_type=assets.Collection)
    return
""",
                }
            ],
        },
    )
    assert response.status_code == 200
    project = app.state.container.project_service.require_project()
    declarations = [
        AssetDeclaration(
            node_id='producer',
            name=name,
            title=name,
            description=None,
            declared_asset_type='collection' if name == 'collection' else 'markdown',
            declaration_index=index,
        )
        for index, name in enumerate(['first', 'second', 'collection'])
    ]
    project.state_db.replace_asset_declarations('producer', 'source', declarations)
    for declaration in declarations:
        project.state_db.ensure_asset_head('producer', declaration.name)
    return client, project.state_db, declarations


def annotate(client, name='first', node_id='producer', **patch):
    response = client.patch(f'/api/v1/assets/{node_id}/{name}/annotation', json=patch)
    assert response.status_code == 200, response.text
    return response.json()['annotation']


def favorites(client):
    response = client.get('/api/v1/dashboards/@favorites')
    assert response.status_code == 200, response.text
    return response.json()


def test_annotations_and_favorites_are_project_global_and_persistent(asset_project):
    client, db, _ = asset_project
    assert favorites(client)['panels'] == []
    assert annotate(client, is_favorite=True, note='A note\non two lines') == {
        'is_favorite': True,
        'note': 'A note\non two lines',
    }
    assert client.get('/api/v1/nodes/producer/assets/first').json()['annotation']['note'] == 'A note\non two lines'
    assert client.get('/api/v1/nodes/producer/assets').json()[0]['annotation']['is_favorite'] is True
    assert db.get_favorites_dashboard()['panels'][0]['asset_name'] == 'first'
    reopened = StateDB(db.path)
    assert reopened.list_asset_annotations('producer')['first'] == {
        'is_favorite': True,
        'note': 'A note\non two lines',
    }
    assert client.get('/api/v1/graph').json()['nodes'][0]['id'] == 'producer'
    assert len(client.get('/api/v1/graph').json()['nodes']) == 1
    annotate(client, is_favorite=False)
    assert favorites(client)['panels'] == []
    assert annotate(client, note='   ') == {'is_favorite': False, 'note': ''}
    assert client.delete('/api/v1/dashboards/@favorites').status_code == 404


def test_asset_versions_and_notebook_rename_preserve_annotations(asset_project):
    client, db, _ = asset_project
    annotate(client, is_favorite=True, note='Keep me')
    for content in ['version one', 'version two']:
        db.create_asset_version(
            node_id='producer',
            asset_name='first',
            asset_type='markdown',
            interactive=False,
            source_hash='source',
            upstream_code_hash='code',
            upstream_data_hash='data',
            run_id=content,
            lineage_mode=LineageMode.MANAGED,
            definition={'markdown_text': content},
            modifier_schema=[],
            default_modifiers={},
            override_schema_hash='schema',
            warnings=[],
            objects=[],
        )
        assert client.get('/api/v1/assets/producer/first').json()['annotation'] == {
            'is_favorite': True,
            'note': 'Keep me',
        }
    dashboard = favorites(client)
    panel = {**dashboard['panels'][0], 'modifier_overrides': {'test': 1}}
    assert (
        client.patch(
            '/api/v1/dashboards/@favorites',
            json={
                'dashboard_version': dashboard['version'],
                'panels': [panel],
            },
        ).status_code
        == 200
    )
    version = client.get('/api/v1/graph').json()['meta']['graph_version']
    assert (
        client.patch(
            '/api/v1/graph',
            json={
                'graph_version': version,
                'operations': [
                    {'type': 'rename_node', 'node_id': 'producer', 'new_node_id': 'renamed', 'title': 'Renamed'}
                ],
            },
        ).status_code
        == 200
    )
    assert client.get('/api/v1/assets/renamed/first').json()['annotation'] == {'is_favorite': True, 'note': 'Keep me'}
    updated = favorites(client)
    assert updated['sources'] == [{'node_id': 'renamed'}]
    assert updated['panels'][0]['panel_id'] == dashboard['panels'][0]['panel_id']
    assert updated['panels'][0]['node_id'] == 'renamed'
    assert updated['panels'][0]['modifier_overrides'] == {'test': 1}


def test_declaration_removal_and_recreation_clear_annotations(asset_project):
    client, db, declarations = asset_project
    annotate(client, is_favorite=True, note='Discard me')
    stale_dashboard = favorites(client)
    db.replace_asset_declarations('producer', 'removed', declarations[1:])
    assert favorites(client)['panels'] == []
    db.delete_asset_state('producer', 'first')
    db.replace_asset_declarations('producer', 'recreated', declarations)
    db.ensure_asset_head('producer', 'first')
    assert client.get('/api/v1/assets/producer/first').json()['annotation'] == {'is_favorite': False, 'note': ''}
    conflict = client.patch(
        '/api/v1/dashboards/@favorites',
        json={
            'dashboard_version': stale_dashboard['version'],
            'panels': stale_dashboard['panels'],
        },
    )
    assert conflict.status_code == 409
    retry = client.patch(
        '/api/v1/dashboards/@favorites',
        json={
            'dashboard_version': conflict.json()['dashboard']['version'],
            'panels': stale_dashboard['panels'],
        },
    )
    assert retry.status_code == 200
    assert retry.json()['panels'] == []


def test_notebook_deletion_clears_annotations_even_when_state_is_tombstoned(asset_project):
    client, db, _ = asset_project
    annotate(client, is_favorite=True, note='Discard me')
    version = client.get('/api/v1/graph').json()['meta']['graph_version']
    deleted = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': version,
            'operations': [{'type': 'delete_node', 'node_id': 'producer'}],
        },
    )
    assert deleted.status_code == 200
    assert db.list_asset_annotations('producer') == {}
    assert favorites(client)['panels'] == []
    stale_panel = {
        'panel_id': 'producer/first',
        'node_id': 'producer',
        'asset_name': 'first',
        'modifier_overrides': {'stale': True},
    }
    assert (
        client.patch(
            '/api/v1/dashboards/@favorites',
            json={
                'dashboard_version': favorites(client)['version'],
                'panels': [stale_panel],
            },
        ).status_code
        == 200
    )
    recreated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': deleted.json()['graph']['meta']['graph_version'],
            'operations': [{'type': 'add_notebook_node', 'node_id': 'producer', 'title': 'Recreated'}],
        },
    )
    assert recreated.status_code == 200
    db.ensure_asset_head('producer', 'first')
    assert client.get('/api/v1/assets/producer/first').json()['annotation'] == {'is_favorite': False, 'note': ''}


def test_favorite_order_overrides_and_membership_are_independent(asset_project):
    client, _, _ = asset_project
    annotate(client, 'second', is_favorite=True)
    annotate(client, 'first', is_favorite=True)
    annotate(client, 'collection', is_favorite=True)
    dashboard = favorites(client)
    assert [panel['asset_name'] for panel in dashboard['panels']] == ['second', 'first', 'collection']
    saved = client.post('/api/v1/nodes/producer/dashboards', json={'title': 'Other dashboard'}).json()
    other_panels = [{**panel, 'modifier_overrides': {'other': True}} for panel in saved['panels']]
    assert (
        client.patch(
            f'/api/v1/dashboards/{saved["dashboard_id"]}',
            json={
                'dashboard_version': saved['version'],
                'panels': other_panels,
            },
        ).status_code
        == 200
    )
    panels = [
        {**panel, 'position': index, 'modifier_overrides': {'favorite': True}}
        for index, panel in enumerate(reversed(dashboard['panels']))
    ]
    updated = client.patch(
        '/api/v1/dashboards/@favorites',
        json={
            'dashboard_version': dashboard['version'],
            'panels': panels,
        },
    )
    assert updated.status_code == 200
    assert [panel['asset_name'] for panel in updated.json()['panels']] == ['collection', 'first', 'second']
    assert client.get(f'/api/v1/dashboards/{saved["dashboard_id"]}').json()['panels'] == other_panels
    annotate(client, 'first', is_favorite=False)
    annotate(client, 'first', is_favorite=True)
    assert favorites(client)['panels'][-1]['asset_name'] == 'first'
    assert favorites(client)['panels'][-1]['modifier_overrides'] == {}
    fresh_dashboard = favorites(client)
    assert fresh_dashboard['panels'][-1]['panel_id'] != dashboard['panels'][1]['panel_id']
    assert (
        client.patch(
            '/api/v1/dashboards/@favorites',
            json={
                'dashboard_version': fresh_dashboard['version'],
                'panels': updated.json()['panels'],
            },
        ).status_code
        == 200
    )
    assert favorites(client)['panels'][-1]['modifier_overrides'] == {}
    assert client.patch('/api/v1/assets/producer/child/annotation', json={'is_favorite': True}).status_code == 404


def test_simultaneous_favorites_append_without_duplicate_positions(asset_project):
    client, db, _ = asset_project
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(
            executor.map(
                lambda name: db.patch_asset_annotation('producer', name, is_favorite=True),
                ['first', 'second', 'collection'],
            )
        )
    dashboard = favorites(client)
    assert len(dashboard['panels']) == 3
    assert [panel['position'] for panel in dashboard['panels']] == [0, 1, 2]
    version = dashboard['version']
    annotate(client, is_favorite=True)
    assert favorites(client)['version'] == version


@pytest.mark.parametrize('removal', ['rename', 'cell'])
def test_notebook_reparse_clears_annotations_on_removed_declarations(asset_project, removal):
    client, _, _ = asset_project
    annotate(client, is_favorite=True, note='Discard me')
    service = client.app.state.container.project_service
    notebook = service.require_project().paths.notebook_path('producer')
    source = notebook.read_text(encoding='utf-8')
    declaration = "    assets.push(assets.Markdown('first'), name='first', title='first')"
    edited = (
        source.replace("name='first'", "name='replacement'") if removal == 'rename' else source.replace(declaration, '')
    )
    notebook.write_text(edited, encoding='utf-8')
    service.reparse_notebook_by_path(notebook)
    assert favorites(client)['panels'] == []
    notebook.write_text(source, encoding='utf-8')
    service.reparse_notebook_by_path(notebook)
    assert client.get('/api/v1/assets/producer/first').json()['annotation'] == {'is_favorite': False, 'note': ''}
