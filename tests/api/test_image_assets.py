import pandas as pd
import pytest
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.assets.registry import serialize_asset
from bulletjournal.domain.enums import LineageMode
from bulletjournal.runtime import assets
from bulletjournal.storage.project_fs import init_project_root


def publish_asset(project, asset, name):
    serialized = serialize_asset(asset, object_store=project.object_store, title=name.title(), description=None)
    objects = []
    for item in serialized.objects:
        persisted = item.persisted
        project.state_db.upsert_artifact_object(
            persisted['artifact_hash'],
            persisted['storage_kind'],
            persisted['data_type'],
            persisted['size_bytes'],
            persisted.get('extension'),
            persisted.get('mime_type'),
            persisted.get('preview'),
        )
        objects.append(
            {
                'object_role': item.object_role,
                'object_index': item.object_index,
                'artifact_hash': persisted['artifact_hash'],
                'metadata': item.metadata,
            }
        )
    return project.state_db.create_asset_version(
        node_id='producer',
        asset_name=name,
        asset_type=serialized.asset_type,
        interactive=serialized.interactive,
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='data',
        run_id='image-run',
        lineage_mode=LineageMode.MANAGED,
        definition=serialized.definition,
        modifier_schema=serialized.modifier_schema,
        default_modifiers=serialized.default_modifiers,
        override_schema_hash='schema',
        warnings=[],
        objects=objects,
    )


@pytest.fixture
def image_project(tmp_path):
    app = create_app(project_path=init_project_root(tmp_path / 'project').root)
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
    assets.push(assets.Collection(), name='gallery', title='Gallery', asset_type=assets.Collection)
    assets.push(assets.Markdown('Note'), name='note', title='Note', asset_type=assets.Markdown)
    assets.push(None, name='preview', title='Preview', asset_type=assets.Image)
    return
""",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    project = app.state.container.project_service.require_project()
    gallery = assets.Collection()
    gallery.add_asset(assets.Markdown('Notes'), name='notes')
    frame = pd.DataFrame({'category': ['a'], 'value': [7]})
    gallery.add_asset(assets.BarChart(frame, category='category', value='value'), name='chart')
    gallery.add_asset(assets.DataFrame(frame), name='table')
    contents = []
    for index in range(2):
        source = tmp_path / f'image_{index}.svg'
        content = f'<svg xmlns="http://www.w3.org/2000/svg" width="{index + 1}" height="2"/>'.encode()
        source.write_bytes(content)
        preview_image = assets.Image(source)
        gallery.add_asset(preview_image, name=f'image_{index}')
        source.unlink()
        contents.append(content)
    asset_version_id = publish_asset(project, gallery, 'gallery')
    publish_asset(project, preview_image, 'preview')
    publish_asset(project, assets.Markdown('Note'), 'note')
    return client, project, asset_version_id, contents


def test_collection_image_delivery_preserves_contents_extension_and_releases_lease(image_project) -> None:
    client, project, version, contents = image_project
    for index, content in enumerate(contents):
        response = client.get(
            '/api/v1/assets/producer/gallery/content',
            params={'asset_version_id': version, 'collection_child_name': f'image_{index}'},
        )
        assert response.status_code == 200, response.text
        assert response.content == content
        assert response.headers['content-type'] == 'image/svg+xml'
        assert 'gallery.svg' in response.headers['content-disposition']
        assert response.headers['content-disposition'].startswith('inline;')
    with project.state_db._connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM object_leases WHERE owner_kind = 'asset_image'").fetchone()[0] == 0
        )


def test_image_delivery_rejects_wrong_version_or_unknown_asset(image_project) -> None:
    client, _, version, _ = image_project
    assert (
        client.get(
            '/api/v1/assets/producer/gallery/content',
            params={
                'asset_version_id': version + 1,
                'collection_child_name': 'image_0',
            },
        ).status_code
        == 400
    )
    assert client.get(f'/api/v1/assets/producer/missing/content?asset_version_id={version}').status_code == 404


@pytest.mark.parametrize('child_name', [None, '', 'missing', 'notes', 'chart', 'table'])
def test_image_delivery_rejects_missing_unknown_or_nonimage_children(image_project, child_name) -> None:
    client, _, version, _ = image_project
    params = {'asset_version_id': version}
    if child_name is not None:
        params['collection_child_name'] = child_name
    response = client.get('/api/v1/assets/producer/gallery/content', params=params)
    assert response.status_code == 400, response.text


def test_standalone_image_content_does_not_require_a_child_name(image_project) -> None:
    client, project, _, contents = image_project
    version = project.state_db.get_asset_head('producer', 'preview')['current_asset_version_id']
    response = client.get(f'/api/v1/assets/producer/preview/content?asset_version_id={version}')
    assert response.status_code == 200, response.text
    assert response.content == contents[-1]
    assert 'preview.svg' in response.headers['content-disposition']
    assert (
        client.get(
            '/api/v1/assets/producer/preview/content',
            params={
                'asset_version_id': version,
                'collection_child_name': 'image_0',
            },
        ).status_code
        == 400
    )


def test_nonimage_asset_content_is_rejected(image_project) -> None:
    client, project, _, _ = image_project
    version = project.state_db.get_asset_head('producer', 'note')['current_asset_version_id']
    assert client.get(f'/api/v1/assets/producer/note/content?asset_version_id={version}').status_code == 400


def test_collection_images_are_selected_by_name_after_children_are_reordered(image_project, tmp_path) -> None:
    client, project, _, contents = image_project
    gallery = assets.Collection()
    for index in reversed(range(2)):
        source = tmp_path / f'image_{index}.svg'
        source.write_bytes(contents[index])
        gallery.add_asset(assets.Image(source), name=f'image_{index}')
    gallery.add_asset(assets.Markdown('Notes'), name='notes')
    version = publish_asset(project, gallery, 'gallery')
    for index, content in enumerate(contents):
        response = client.get(
            '/api/v1/assets/producer/gallery/content',
            params={
                'asset_version_id': version,
                'collection_child_name': f'image_{index}',
            },
        )
        assert response.status_code == 200, response.text
        assert response.content == content


def test_prepare_still_resolves_named_children_in_mixed_collection(image_project) -> None:
    client, _, version, _ = image_project
    for child_name in ['table', 'chart']:
        response = client.post(
            '/api/v1/assets/producer/gallery/prepare',
            json={
                'asset_version_id': version,
                'panel_context': {'collection_child_name': child_name},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()['payloads']['table']['rows'] == [{'category': 'a', 'value': 7}]
