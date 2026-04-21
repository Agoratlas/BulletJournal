from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


def test_open_and_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    response = client.get('/api/v1/project/snapshot')
    assert response.status_code == 200

    project_id = response.json()['project']['project_id']
    snapshot = client.get('/api/v1/project/snapshot')
    assert snapshot.status_code == 200
    assert snapshot.json()['project']['project_id'] == project_id
    assert 'notices' in snapshot.json()


def test_node_detail_endpoint_available_at_project_nodes_path(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
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

    detail = client.get('/api/v1/nodes/sample_node')

    assert detail.status_code == 200
    assert detail.json()['id'] == 'sample_node'


def test_new_notebook_is_custom_and_uses_empty_template_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'custom_node',
                    'title': 'Custom Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'custom_node')

    assert node['template'] is None

    notebook = client.get('/api/v1/nodes/custom_node/notebook/download')
    assert notebook.status_code == 200
    source = notebook.text
    assert 'from bulletjournal.runtime import artifacts' in source
    assert 'import marimo as mo' in source
    assert 'import pandas as pd' in source


def test_graph_patch_rejects_unknown_operation_fields(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    invalid = client.patch(
        '/api/v1/graph',
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

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/graph',
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

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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

    snapshot = client.get('/api/v1/project/snapshot').json()
    warning = next(issue for issue in snapshot['validation_issues'] if issue['severity'] == 'warning')
    assert any(issue['issue_id'] == warning['issue_id'] for issue in snapshot['notices'])

    dismissed = client.post(f'/api/v1/notices/{warning["issue_id"]}/dismiss')

    assert dismissed.status_code == 200
    refreshed = client.get('/api/v1/project/snapshot').json()
    assert all(issue['issue_id'] != warning['issue_id'] for issue in refreshed['notices'])


def test_error_notice_can_be_dismissed_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
    notebook_path.write_text(notebook_path.read_text(encoding='utf-8') + '\nbroken =\n', encoding='utf-8')
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    error_issue = next(issue for issue in snapshot['validation_issues'] if issue['severity'] == 'error')

    dismissed = client.post(f'/api/v1/notices/{error_issue["issue_id"]}/dismiss')

    assert dismissed.status_code == 200
    refreshed = client.get('/api/v1/project/snapshot').json()
    assert all(issue['issue_id'] != error_issue['issue_id'] for issue in refreshed['notices'])


def test_run_session_can_be_stopped_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']

    stopped = client.post(f'/api/v1/sessions/{session_id}/stop')
    assert stopped.status_code == 200
    assert stopped.json()['status'] == 'stopped'
    assert container.run_service.session_manager.get(session_id) is None


def test_file_input_artifact_name_round_trips_in_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'uploaded_file')
    assert node['ui']['artifact_name'] == 'dataset'
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_invalid_notebook_changes_keep_previous_ports_and_surface_errors(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
    original_source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        original_source.replace(
            "artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, description='Sample output frame')",
            "artifacts.push(frame, name='renamed_df', data_type=pd.DataFrame, description='Sample output frame')\n    broken =",
        ),
        encoding='utf-8',
    )

    container = app.state.container
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'sample_node')

    assert [port['name'] for port in node['interface']['outputs']] == ['sample_df']
    assert any(
        issue['code'] == 'invalid_syntax'
        for issue in snapshot['validation_issues']
        if issue['node_id'] == 'sample_node'
    )
    assert node['state'] == 'error'


def test_unparsable_marimo_cell_keeps_previous_ports_and_surfaces_errors(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
    original_source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        original_source.replace(
            "@app.cell\ndef _(pd, sample_count):\n    frame = pd.DataFrame({'value': list(range(sample_count))})\n    artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, description='Sample output frame')\n    return frame",
            'app._unparsable_cell(\n    r"""\nframe = pd.DataFrame({\'value\': list(range(sample_count))})\nartifacts.push(frame, name=\'renamed_df\', data_type=pd.DataFrame, description=\'Sample output frame\')\nbroken =\nreturn frame\n"""\n)',
        ),
        encoding='utf-8',
    )

    container = app.state.container
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'sample_node')

    assert [port['name'] for port in node['interface']['outputs']] == ['sample_df']
    assert any(
        issue['code'] == 'invalid_syntax'
        for issue in snapshot['validation_issues']
        if issue['node_id'] == 'sample_node'
    )
    assert node['state'] == 'error'


def test_graph_patch_accepts_inline_notebook_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    notebook_source = (
        """
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
    artifacts.push(value, name='value', data_type=int)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )

    created = client.patch(
        '/api/v1/graph',
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


def test_snapshot_includes_pipeline_templates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')

    assert opened.status_code == 200
    templates = opened.json()['templates']
    pipeline = next(item for item in templates if item['kind'] == 'pipeline')
    assert pipeline['ref'] == 'examples/example_iris_pipeline'
    assert pipeline['definition']['nodes']


def test_graph_patch_can_add_and_update_organizer_node(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_organizer_node',
                    'node_id': 'organizer',
                    'title': 'Organizer',
                    'x': 240,
                    'y': 180,
                }
            ],
        },
    )

    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_organizer_ports',
                    'node_id': 'organizer',
                    'ports': [
                        {'key': 'dataset', 'name': 'dataset', 'data_type': 'file'},
                        {'key': 'count', 'name': 'sample_count', 'data_type': 'int'},
                    ],
                }
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    organizer = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'organizer')

    assert organizer['kind'] == 'organizer'
    assert organizer['ui']['organizer_ports'][1]['name'] == 'sample_count'
    assert [port['name'] for port in organizer['interface']['inputs']] == ['dataset', 'count']
    assert organizer['interface']['inputs'][1]['label'] == 'sample_count'


def test_graph_patch_can_add_edge_to_new_organizer_port_in_same_request(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'source',
                    'title': 'Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_organizer_node',
                    'node_id': 'organizer',
                    'title': 'Organizer',
                    'x': 240,
                    'y': 180,
                },
            ],
        },
    )
    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_organizer_ports',
                    'node_id': 'organizer',
                    'ports': [
                        {'key': 'value', 'name': 'iris_dataframe', 'data_type': 'int'},
                    ],
                },
                {
                    'type': 'add_edge',
                    'source_node': 'source',
                    'source_port': 'value',
                    'target_node': 'organizer',
                    'target_port': 'value',
                },
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    assert any(
        edge['source_node'] == 'source'
        and edge['source_port'] == 'value'
        and edge['target_node'] == 'organizer'
        and edge['target_port'] == 'value'
        for edge in snapshot['graph']['edges']
    )


def test_graph_patch_can_add_and_style_area_node(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_area_node',
                    'node_id': 'area',
                    'title': 'Ingestion',
                    'x': 120,
                    'y': 160,
                    'w': 480,
                    'h': 280,
                }
            ],
        },
    )

    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_area_style',
                    'node_id': 'area',
                    'title_position': 'bottom-center',
                    'color': 'purple',
                    'filled': False,
                }
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    area = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'area')
    layout = next(entry for entry in snapshot['graph']['layout'] if entry['node_id'] == 'area')

    assert area['kind'] == 'area'
    assert area['title'] == 'Ingestion'
    assert area['ui']['title_position'] == 'bottom-center'
    assert area['ui']['area_color'] == 'purple'
    assert area['ui']['area_filled'] is False
    assert layout['w'] == 480
    assert layout['h'] == 280


def test_graph_patch_can_add_pipeline_template(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                    'x': 200,
                    'y': 240,
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert {'file', 'example_1', 'example_2', 'example_3', 'example_4'} <= node_ids
    edge_ids = {edge['id'] for edge in snapshot['graph']['edges']}
    assert 'file.file__example_1.iris_csv' in edge_ids
    layout_by_node = {entry['node_id']: entry for entry in snapshot['graph']['layout']}
    assert layout_by_node['file']['x'] == 200
    assert layout_by_node['example_4']['y'] == 240
    assert layout_by_node['file']['y'] - layout_by_node['example_4']['y'] == 180


def test_graph_patch_requires_prefix_when_pipeline_template_collides(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )
    assert created.status_code == 200

    duplicate = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )

    assert duplicate.status_code == 409
    assert 'Use a prefix to instantiate it' in duplicate.json()['detail']


def test_graph_patch_accepts_prefixed_pipeline_template(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    first = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )
    assert first.status_code == 200

    second = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': first.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                    'node_id_prefix': 'study_b',
                }
            ],
        },
    )

    assert second.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert {
        'study_b_file',
        'study_b_example_1',
        'study_b_example_2',
        'study_b_example_3',
        'study_b_example_4',
    } <= node_ids


def test_file_input_node_can_use_custom_artifact_name(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
    snapshot = client.get('/api/v1/project/snapshot')
    node = next(item for item in snapshot.json()['graph']['nodes'] if item['id'] == 'source_file')
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_artifact_download_uses_artifact_name_and_extension(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download')

    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'filename="sample_df.parquet"' in response.headers['content-disposition']
    assert response.headers['content-type']


def test_dataframe_csv_download_returns_attachment(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download?format=csv')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert 'filename="sample_df.csv"' in response.headers['content-disposition']
    assert b'value\n' in response.content


def test_dataframe_csv_download_rejects_large_artifacts(tmp_path, monkeypatch) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    from bulletjournal.services import artifact_service as artifact_service_module

    monkeypatch.setattr(artifact_service_module, 'DATAFRAME_CSV_DOWNLOAD_MAX_BYTES', 1)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download?format=csv')

    assert response.status_code == 400
    assert '100 MB' in response.text


def test_file_artifact_content_endpoint_renders_inline_image(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/file-inputs/image_source/upload',
        content=png_bytes,
        headers={
            'X-Filename': 'chart upload.png',
            'Content-Type': 'image/png',
        },
    )
    assert upload.status_code == 200

    artifact = client.get('/api/v1/artifacts/image_source/preview_image')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    assert preview['kind'] == 'file'
    assert preview['image_inline'] is True
    assert preview['mime_type'] == 'image/png'

    content = client.get('/api/v1/artifacts/image_source/preview_image/content')

    assert content.status_code == 200
    assert content.headers['content-type'].startswith('image/png')
    assert 'attachment' not in content.headers.get('content-disposition', '')
    assert content.content == png_bytes


def test_notebook_download_endpoint_returns_python_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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

    response = client.get('/api/v1/nodes/sample_node/notebook/download')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/x-python')
    assert 'filename="sample_node.py"' in response.headers['content-disposition']
    assert 'import marimo' in response.text


def test_artifact_state_endpoints_can_mark_outputs_stale_and_ready(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    stale = client.post(
        '/api/v1/artifacts/sample_node/sample_df/state',
        json={'state': 'stale'},
    )
    assert stale.status_code == 200
    assert stale.json()['state'] == 'stale'

    ready = client.post(
        '/api/v1/artifacts/sample_node/sample_df/state',
        json={'state': 'ready'},
    )
    assert ready.status_code == 200
    assert ready.json()['state'] == 'ready'

    bulk_stale = client.post(
        '/api/v1/nodes/sample_node/outputs/state',
        json={'state': 'stale'},
    )
    assert bulk_stale.status_code == 200
    assert 'sample_df' in bulk_stale.json()['artifact_names']

    bulk_ready = client.post(
        '/api/v1/nodes/sample_node/outputs/state',
        json={'state': 'ready', 'only_current_state': 'stale'},
    )
    assert bulk_ready.status_code == 200
    assert 'sample_df' in bulk_ready.json()['artifact_names']

    refreshed = client.get('/api/v1/artifacts/sample_node/sample_df')
    assert refreshed.status_code == 200
    assert refreshed.json()['state'] == 'ready'


def test_marking_node_outputs_stale_also_stales_downstream_nodes(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    bulk_stale = client.post(
        '/api/v1/nodes/value_source/outputs/state',
        json={'state': 'stale'},
    )
    assert bulk_stale.status_code == 200

    upstream = client.get('/api/v1/artifacts/value_source/value')
    downstream = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert upstream.status_code == 200
    assert downstream.status_code == 200
    assert upstream.json()['state'] == 'stale'
    assert downstream.json()['state'] == 'stale'


def test_marking_outputs_ready_is_blocked_when_inputs_are_stale(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200

    stale_source = client.post(
        '/api/v1/nodes/value_source/outputs/state',
        json={'state': 'stale'},
    )
    assert stale_source.status_code == 200

    blocked = client.post(
        '/api/v1/nodes/table_sink/outputs/state',
        json={'state': 'ready', 'only_current_state': 'stale'},
    )
    assert blocked.status_code == 400
    assert 'stale or pending inputs' in blocked.json()['detail']


def test_frozen_block_blocks_upstream_graph_edits_and_editor_sessions(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['table_sink']['ui']['frozen'] is True
    assert nodes['value_source']['ui']['frozen'] is True

    blocked_edit = client.post(
        '/api/v1/nodes/value_source/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert blocked_edit.status_code == 400
    assert 'frozen block' in blocked_edit.json()['detail']
    assert 'table_sink' in blocked_edit.json()['detail']

    blocked_graph_edit = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': frozen.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'remove_edge',
                    'edge_id': 'value_source.value__table_sink.sample_count',
                }
            ],
        },
    )
    assert blocked_graph_edit.status_code == 409
    assert 'frozen block' in blocked_graph_edit.json()['detail']
    assert 'table_sink' in blocked_graph_edit.json()['detail']


def test_freezing_notebook_is_blocked_when_upstream_editor_is_open(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    started = client.post(
        '/api/v1/nodes/value_source/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 409
    assert 'upstream editor' in frozen.json()['detail']
    assert 'value_source' in frozen.json()['detail']


def test_unfreezing_upstream_notebook_also_unfreezes_frozen_descendants(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    unfrozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': frozen.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'value_source',
                    'frozen': False,
                }
            ],
        },
    )
    assert unfrozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['value_source']['ui']['frozen'] is False
    assert nodes['table_sink']['ui']['frozen'] is False


def test_freezing_downstream_block_also_freezes_upstream_file_blocks(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'source_text': (
                        'import marimo\n\n'
                        'app = marimo.App()\n\n'
                        'with app.setup:\n'
                        '    from bulletjournal.runtime import artifacts\n\n'
                        '@app.cell\n'
                        'def _():\n'
                        "    file_path = artifacts.pull_file(name='incoming')\n"
                        '    return file_path\n\n'
                        '@app.cell\n'
                        'def _(file_path):\n'
                        "    artifacts.push(len(file_path), name='path_length', data_type=int)\n"
                        '    return\n\n'
                        "if __name__ == '__main__':\n"
                        '    from bulletjournal.runtime.standalone import run_notebook_app\n\n'
                        '    run_notebook_app(app, __file__)\n'
                    ),
                },
            ],
        },
    )
    assert created.status_code == 200

    container.project_service.reparse_notebook_by_path(project_root / 'notebooks' / 'table_sink.py')

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'source_file',
                    'source_port': 'file',
                    'target_node': 'table_sink',
                    'target_port': 'incoming',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['source_file']['ui']['frozen'] is True
    assert nodes['table_sink']['ui']['frozen'] is True


def test_frozen_file_input_blocks_upload_and_shows_frozen_state(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                }
            ],
        },
    )
    assert created.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'source_file',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['source_file']['ui']['frozen'] is True

    blocked = client.post(
        '/api/v1/file-inputs/source_file/upload',
        data=b'hello world',
        headers={
            'content-type': 'text/plain',
            'x-filename': 'hello.txt',
        },
    )
    assert blocked.status_code == 400
    assert 'frozen block' in blocked.json()['detail']
    assert 'frozen' in blocked.json()['detail']


def test_deleting_node_stops_active_editor_session(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']
    assert container.run_service.session_manager.get(session_id) is not None

    deleted = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'delete_node',
                    'node_id': 'sample_node',
                }
            ],
        },
    )
    assert deleted.status_code == 200
    assert container.run_service.session_manager.get(session_id) is None


def test_freezing_node_stops_active_editor_session(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
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

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']
    assert container.run_service.session_manager.get(session_id) is not None

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'sample_node',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200
    assert container.run_service.session_manager.get(session_id) is None
