from pathlib import Path

from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


def test_open_and_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    response = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    assert response.status_code == 200

    project_id = response.json()['project']['project_id']
    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot')
    assert snapshot.status_code == 200
    assert snapshot.json()['project']['project_id'] == project_id
    assert 'notices' in snapshot.json()


def test_node_detail_endpoint_available_at_project_nodes_path(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    detail = client.get(f'/api/v1/projects/{project_id}/nodes/sample_node')

    assert detail.status_code == 200
    assert detail.json()['id'] == 'sample_node'


def test_graph_patch_rejects_unknown_operation_fields(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    invalid = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                    'unexpected': 'nope',
                }
            ],
        },
    )

    assert invalid.status_code == 422


def test_cors_allows_local_origin_only(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    allowed = client.get('/healthz', headers={'Origin': 'http://localhost:8765'})
    blocked = client.get('/healthz', headers={'Origin': 'https://example.com'})

    assert allowed.headers.get('access-control-allow-origin') == 'http://localhost:8765'
    assert blocked.headers.get('access-control-allow-origin') is None


def test_graph_layout_patch_accepts_position_only_updates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                    'x': 100,
                    'y': 120,
                    'w': 480,
                    'h': 260,
                }
            ],
        },
    )
    assert created.status_code == 200

    moved = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_layout',
                    'node_id': 'sample_node',
                    'x': 220,
                    'y': 260,
                }
            ],
        },
    )

    assert moved.status_code == 200
    layout = next(item for item in moved.json()['layout'] if item['node_id'] == 'sample_node')
    assert layout['x'] == 220
    assert layout['y'] == 260
    assert layout['w'] == 480
    assert layout['h'] == 260


def test_warning_notice_can_be_dismissed_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        source.replace(
            "sample_count = artifacts.pull(name='sample_count', data_type=int, default=10)",
            "sample_count = artifacts.pull(name='sample_count', data_type='mystery', default=10)",
        ),
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    warning = next(issue for issue in snapshot['validation_issues'] if issue['severity'] == 'warning')
    assert any(issue['issue_id'] == warning['issue_id'] for issue in snapshot['notices'])

    dismissed = client.post(f"/api/v1/projects/{project_id}/notices/{warning['issue_id']}/dismiss")

    assert dismissed.status_code == 200
    refreshed = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    assert all(issue['issue_id'] != warning['issue_id'] for issue in refreshed['notices'])


def test_file_input_artifact_name_round_trips_in_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'uploaded_file',
                    'title': 'Uploaded File',
                    'artifact_name': 'dataset',
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'uploaded_file')
    assert node['ui']['artifact_name'] == 'dataset'
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_graph_patch_accepts_inline_notebook_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    notebook_source = """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    value = 7
    return value

@app.cell
def _(value):
    artifacts.push(value, name='value', data_type=int, is_output=True)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip() + '\n'

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'inline_source',
                    'title': 'Inline Source',
                    'source_text': notebook_source,
                }
            ],
        },
    )

    assert created.status_code == 200
    notebook_path = project_root / 'notebooks' / 'inline_source.py'
    assert notebook_path.read_text(encoding='utf-8') == notebook_source


def test_file_input_node_can_use_custom_artifact_name(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                    'artifact_name': 'dataset',
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get(f'/api/v1/projects/{project_id}/snapshot')
    node = next(item for item in snapshot.json()['graph']['nodes'] if item['id'] == 'source_file')
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_artifact_download_uses_artifact_name_and_extension(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post(
        f'/api/v1/projects/{project_id}/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get(f'/api/v1/projects/{project_id}/artifacts/sample_node/sample_df/download')

    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'filename="sample_df.parquet"' in response.headers['content-disposition']
    assert response.headers['content-type']


def test_file_artifact_content_endpoint_renders_inline_image(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.post('/api/v1/projects/open', json={'path': str(project_root)})
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        f'/api/v1/projects/{project_id}/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'image_source',
                    'title': 'Image Source',
                    'artifact_name': 'preview_image',
                }
            ],
        },
    )
    assert created.status_code == 200

    png_bytes = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
        b'\x1f\x15\xc4\x89'
        b'\x00\x00\x00\x0cIDATx\x9cc``\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00'
        b'\x18\xdd\x8d\xb1'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    upload = client.post(
        f'/api/v1/projects/{project_id}/file-inputs/image_source/upload',
        content=png_bytes,
        headers={
            'X-Filename': 'chart upload.png',
            'Content-Type': 'image/png',
        },
    )
    assert upload.status_code == 200

    artifact = client.get(f'/api/v1/projects/{project_id}/artifacts/image_source/preview_image')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    assert preview['kind'] == 'file'
    assert preview['image_inline'] is True
    assert preview['mime_type'] == 'image/png'

    content = client.get(f'/api/v1/projects/{project_id}/artifacts/image_source/preview_image/content')

    assert content.status_code == 200
    assert content.headers['content-type'].startswith('image/png')
    assert 'attachment' not in content.headers.get('content-disposition', '')
    assert content.content == png_bytes
