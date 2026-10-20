import ast
import time

import pytest
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.domain.type_system import CANONICAL_TYPES
from bulletjournal.storage.project_fs import init_project_root


def test_create_notebook_with_ordered_inputs_and_connections_in_one_patch(tmp_path):
    root = init_project_root(tmp_path / 'project').root
    with TestClient(create_app(project_path=root)) as client:
        version = client.get('/api/v1/graph').json()['meta']['graph_version']
        response = client.patch(
            '/api/v1/graph',
            json={
                'graph_version': version,
                'operations': [
                    {'type': 'add_constant_node', 'node_id': 'source', 'data_type': 'int', 'value': 12},
                    {
                        'type': 'add_notebook_node',
                        'node_id': 'custom',
                        'title': 'Custom',
                        'inputs': [
                            {'name': 'renamed', 'data_type': 'int', 'description': 'A count'},
                            {'name': 'optional', 'data_type': 'pandas.DataFrame', 'default_to_none': True},
                            {'name': 'document', 'data_type': 'file', 'default_to_none': True},
                        ],
                    },
                    {
                        'type': 'add_edge',
                        'source_node': 'source',
                        'source_port': 'value',
                        'target_node': 'custom',
                        'target_port': 'renamed',
                    },
                ],
            },
        )
        assert response.status_code == 200, response.text
        graph = response.json()['graph']
        node = next(node for node in graph['nodes'] if node['id'] == 'custom')
        assert node['template'] is None
        assert 'input_labels' not in node['ui']
        inputs = node['interface']['inputs']
        assert [port['name'] for port in inputs] == ['renamed', 'optional', 'document']
        assert [port['data_type'] for port in inputs] == ['int', 'pandas.DataFrame', 'file']
        assert [port['has_default'] for port in inputs] == [False, True, True]
        assert inputs[0]['description'] == 'A count'
        assert node['interface']['outputs'] == []
        assert not node['interface']['issues']
        assert len(graph['edges']) == 1
        source = (root / 'notebooks' / 'custom.py').read_text()
        ast.parse(source)
        assert source.count('@app.cell') == 5
        assert "app_title='custom'" in source
        assert 'renamed = artifacts.pull(\n        name=' in source
        assert 'document = artifacts.pull_file(\n' in source

        # The generated notebook must also load as a valid marimo app.
        from marimo._ast.load import load_app

        app = load_app(root / 'notebooks' / 'custom.py')
        assert app is not None
        assert len(list(app._cell_manager.cells())) == 6  # Includes setup.
        run = client.post('/api/v1/nodes/custom/run', json={'mode': 'run_all'})
        assert run.status_code == 200, run.text
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = client.get('/api/v1/project/snapshot').json()
            record = next(item for item in snapshot['runs'] if item['run_id'] == run.json()['run_id'])
            if record['status'] in {'succeeded', 'failed', 'cancelled'}:
                break
            time.sleep(0.05)
        assert record['status'] == 'succeeded', record


@pytest.mark.parametrize(
    'inputs,extra',
    [
        ([{'name': 'BAD', 'data_type': 'int'}], {}),
        ([{'name': 'same', 'data_type': 'int'}] * 2, {}),
        ([{'name': 'a', 'data_type': 'invalid'}], {}),
        ([], {'template_ref': 'builtin/empty_notebook'}),
        ([], {'source_text': 'import marimo'}),
    ],
)
def test_invalid_draft_creation_does_not_persist_a_notebook(tmp_path, inputs, extra):
    root = init_project_root(tmp_path / 'project').root
    with TestClient(create_app(project_path=root)) as client:
        version = client.get('/api/v1/graph').json()['meta']['graph_version']
        response = client.patch(
            '/api/v1/graph',
            json={
                'graph_version': version,
                'operations': [
                    {'type': 'add_notebook_node', 'node_id': 'custom', 'title': 'Custom', 'inputs': inputs, **extra}
                ],
            },
        )
        assert response.status_code == (422 if inputs and inputs[0]['name'] == 'BAD' else 409), response.text
        assert client.get('/api/v1/graph').json()['nodes'] == []
        assert not (root / 'notebooks' / 'custom.py').exists()


def test_generated_inputs_support_all_canonical_types(tmp_path):
    root = init_project_root(tmp_path / 'project').root
    types = sorted(CANONICAL_TYPES)
    with TestClient(create_app(project_path=root)) as client:
        version = client.get('/api/v1/graph').json()['meta']['graph_version']
        response = client.patch(
            '/api/v1/graph',
            json={
                'graph_version': version,
                'operations': [
                    {
                        'type': 'add_notebook_node',
                        'node_id': 'all_types',
                        'title': 'All types',
                        'inputs': [
                            {
                                'name': 'nx' if index == 0 else f'input_{index}',
                                'data_type': data_type,
                                'default_to_none': True,
                            }
                            for index, data_type in enumerate(types)
                        ],
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        node = response.json()['graph']['nodes'][0]
        assert [port['data_type'] for port in node['interface']['inputs']] == types
        assert node['interface']['issues'] == []
        run = client.post('/api/v1/nodes/all_types/run', json={'mode': 'run_all'})
        assert run.status_code == 200, run.text
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = client.get('/api/v1/project/snapshot').json()
            record = next(item for item in snapshot['runs'] if item['run_id'] == run.json()['run_id'])
            if record['status'] in {'succeeded', 'failed', 'cancelled'}:
                break
            time.sleep(0.05)
        assert record['status'] == 'succeeded', record
