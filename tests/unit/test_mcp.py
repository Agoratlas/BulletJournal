from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from bulletjournal.api.app import create_app
from bulletjournal.config import ServerConfig, mcp_enabled_from_env
from bulletjournal.domain.models import Edge
from bulletjournal.mcp.server import (
    McpGraphOperation,
    _layout_helper,
    _move_nodes_in_rectangle,
    _validate_area_only_resizes,
)
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def publish(self, *args, **kwargs) -> None:
        pass


def _initialize_mcp(client: TestClient, path: str = '/mcp') -> dict[str, str]:
    response = client.post(
        path,
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-11-25',
                'capabilities': {},
                'clientInfo': {'name': 'test', 'version': '1'},
            },
        },
        headers={'accept': 'application/json', 'host': '127.0.0.1:8765'},
    )
    assert response.status_code == 200
    return {
        'accept': 'application/json',
        'host': '127.0.0.1:8765',
        'mcp-session-id': response.headers['mcp-session-id'],
    }


@pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'yes', 'on'])
def test_mcp_enablement_accepts_documented_true_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', value)

    assert mcp_enabled_from_env() is True


@pytest.mark.parametrize('value', ['', '0', 'false', 'no', 'off'])
def test_mcp_enablement_treats_false_values_as_disabled(monkeypatch, value: str) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', value)

    assert mcp_enabled_from_env() is False


def test_mcp_enablement_is_disabled_when_absent(monkeypatch) -> None:
    monkeypatch.delenv('BULLETJOURNAL_ENABLE_MCP', raising=False)

    assert mcp_enabled_from_env() is False


def test_mcp_enablement_rejects_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', 'definitely')

    with pytest.raises(ValueError, match='Invalid BULLETJOURNAL_ENABLE_MCP'):
        mcp_enabled_from_env()


def test_mcp_route_is_disabled_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv('BULLETJOURNAL_ENABLE_MCP', raising=False)
    project = init_project_root(tmp_path / 'project')
    app = create_app(project_path=project.root)

    assert '/mcp' not in [getattr(route, 'path', None) for route in app.routes]


def test_mcp_initialization_and_discovery(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', 'true')
    project = init_project_root(tmp_path / 'project')
    app = create_app(project_path=project.root, server_config=ServerConfig(base_path='/p/demo'))
    request = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2025-11-25',
            'capabilities': {},
            'clientInfo': {'name': 'test', 'version': '1'},
        },
    }

    with TestClient(app) as client:
        response = client.post(
            '/p/demo/mcp',
            json=request,
            headers={'accept': 'application/json', 'host': '127.0.0.1:8765'},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload['result']['serverInfo']['name'] == 'BulletJournal'


def test_mcp_tool_discovery_documents_closed_values_and_workflows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', 'true')
    project = init_project_root(tmp_path / 'project')
    app = create_app(project_path=project.root)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}

    with TestClient(app) as client:
        response = client.post('/mcp', json=request, headers=_initialize_mcp(client))

    assert response.status_code == 200
    tools = {tool['name']: tool for tool in response.json()['result']['tools']}
    start_run = tools['start_run']
    properties = start_run['inputSchema']['properties']
    assert properties['target']['enum'] == ['node', 'selection', 'all_stale']
    assert properties['mode']['enum'] == ['run_stale', 'run_all']
    assert properties['scope']['enum'] == ['node', 'ancestors', 'descendants']
    assert properties['action']['anyOf'][0]['enum'] == ['use_stale', 'run_upstream']
    assert 'confirmation_required' in start_run['description']
    assert tools['list_templates']['inputSchema']['properties']['kind']['anyOf'][0]['enum'] == ['notebook', 'pipeline']
    assert 'graph_version' in tools['apply_graph_changes']['description']
    assert 'cancellation is asynchronous' in tools['cancel_run']['description'].lower()
    assert 'update_node_layout' in tools['apply_graph_changes']['description']
    assert 'move_nodes_in_rectangle' in tools
    assert '20' in tools['move_nodes_in_rectangle']['description']
    assert 'layout_helper' in tools
    source_properties = tools['get_notebook_source']['inputSchema']['properties']
    assert source_properties['offset']['minimum'] == 0
    assert source_properties['limit']['anyOf'][0]['maximum'] == 100
    assert 'inclusive range' in tools['patch_notebook_source']['description'].lower()
    assert 'full-document replacement' in tools['update_notebook_source']['description']
    assert tools['get_execution_logs']['inputSchema']['properties']['stream']['anyOf'][0]['enum'] == [
        'stdout',
        'stderr',
    ]
    assert 'dashboard_version' in tools['update_dashboard']['description']


def test_mcp_resource_discovery_describes_safe_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('BULLETJOURNAL_ENABLE_MCP', 'true')
    project = init_project_root(tmp_path / 'project')
    app = create_app(project_path=project.root)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'resources/list', 'params': {}}

    with TestClient(app) as client:
        headers = _initialize_mcp(client)
        response = client.post('/mcp', json=request, headers=headers)
        templates_response = client.post(
            '/mcp',
            json={'jsonrpc': '2.0', 'id': 2, 'method': 'resources/templates/list', 'params': {}},
            headers=headers,
        )

    assert response.status_code == 200
    resources = {resource['uri']: resource for resource in response.json()['result']['resources']}
    graph = resources['bulletjournal://project/graph']
    assert graph['name'] == 'Project graph'
    assert 'before graph mutations' in graph['description']
    assert resources['bulletjournal://project/validation']['name'] == 'Project validation'
    assert templates_response.status_code == 200
    templates = {item['uriTemplate']: item for item in templates_response.json()['result']['resourceTemplates']}
    assert templates['bulletjournal://nodes/{node_id}/source']['mimeType'] == 'text/x-python'


def test_mcp_graph_operation_schema_accepts_all_supported_graph_operations() -> None:
    adapter = TypeAdapter(McpGraphOperation)

    pipeline = adapter.validate_python({'type': 'add_pipeline_template', 'template_ref': 'provider/pipeline'})

    assert pipeline.template_ref == 'provider/pipeline'
    area = adapter.validate_python({'type': 'add_area_node', 'node_id': 'area', 'title': 'Area'})
    layout = adapter.validate_python({'type': 'update_node_layout', 'node_id': 'area', 'x': 100, 'y': 200})
    rename = adapter.validate_python(
        {'type': 'rename_node', 'node_id': 'area', 'new_node_id': 'renamed_area', 'title': 'Renamed area'}
    )

    assert area.node_id == 'area'
    assert layout.w is None
    assert rename.new_node_id == 'renamed_area'
    with pytest.raises(ValidationError):
        adapter.validate_python({'type': 'not_a_graph_operation'})


def test_mcp_allows_area_only_resizes(tmp_path: Path) -> None:
    project = init_project_root(tmp_path / 'project')
    project_service = ProjectService(event_service=_FakeEventService(), template_service=TemplateService())
    project_service.open_project(project.root)
    graph_service = GraphService(project_service)
    adapter = TypeAdapter(McpGraphOperation)

    assert (
        _validate_area_only_resizes(
            graph_service,
            [
                adapter.validate_python({'type': 'add_area_node', 'node_id': 'area'}),
                adapter.validate_python({'type': 'update_node_layout', 'node_id': 'area', 'x': 10, 'y': 20, 'w': 320}),
            ],
        )
        is None
    )
    assert (
        _validate_area_only_resizes(
            graph_service,
            [
                adapter.validate_python({'type': 'add_notebook_node', 'node_id': 'notebook', 'title': 'Notebook'}),
                adapter.validate_python(
                    {'type': 'update_node_layout', 'node_id': 'notebook', 'x': 10, 'y': 20, 'w': 320}
                ),
            ],
        )
        == 'Only area blocks can be resized. Omit `w` and `h` to move another block.'
    )


def test_mcp_moves_only_nodes_entirely_within_rectangle(tmp_path: Path) -> None:
    project = init_project_root(tmp_path / 'project')
    project_service = ProjectService(event_service=_FakeEventService(), template_service=TemplateService())
    project_service.open_project(project.root)
    graph_service = GraphService(project_service)
    graph_service.apply_operations(
        project_service.graph().meta['graph_version'],
        [
            {'type': 'add_notebook_node', 'node_id': 'inside', 'title': 'Inside', 'x': 20, 'y': 40, 'w': 100, 'h': 80},
            {'type': 'add_notebook_node', 'node_id': 'edge', 'title': 'Edge', 'x': 140, 'y': 40, 'w': 100, 'h': 80},
        ],
    )

    result = _move_nodes_in_rectangle(
        graph_service,
        expected_graph_version=project_service.graph().meta['graph_version'],
        request_id='move-inside',
        x_min=20,
        x_max=120,
        y_min=40,
        y_max=120,
        dx=20,
        dy=40,
    )

    layout = {entry['node_id']: entry for entry in result['graph']['layout']}
    assert layout['inside']['x'] == 40
    assert layout['inside']['y'] == 80
    assert layout['edge']['x'] == 140
    assert layout['edge']['y'] == 40


def test_layout_helper_reports_long_edges_backwards_edges_and_non_area_overlaps(tmp_path: Path) -> None:
    project = init_project_root(tmp_path / 'project')
    project_service = ProjectService(event_service=_FakeEventService(), template_service=TemplateService())
    project_service.open_project(project.root)
    graph_service = GraphService(project_service)
    graph_service.apply_operations(
        project_service.graph().meta['graph_version'],
        [
            {'type': 'add_notebook_node', 'node_id': 'left', 'title': 'Left', 'x': 0, 'y': 0, 'w': 100, 'h': 100},
            {'type': 'add_notebook_node', 'node_id': 'right', 'title': 'Right', 'x': 200, 'y': 0, 'w': 100, 'h': 100},
            {
                'type': 'add_constant_node',
                'node_id': 'overlap',
                'title': 'Overlap',
                'x': 40,
                'y': 40,
                'w': 100,
                'h': 100,
                'data_type': 'int',
            },
            {'type': 'add_area_node', 'node_id': 'area', 'title': 'Area', 'x': 0, 'y': 0, 'w': 400, 'h': 200},
        ],
    )
    graph = project_service.graph()
    graph.edges.append(
        Edge(
            id='right:output->left:input',
            source_node='right',
            source_port='output',
            target_node='left',
            target_port='input',
        )
    )
    project_service.write_graph(graph, increment_version=False)

    result = _layout_helper(graph_service, limit=1)

    assert result['longest_edges'] == [
        {'edge_id': 'right:output->left:input', 'source_node': 'right', 'target_node': 'left', 'length': 200.0}
    ]
    assert result['right_to_left_edges'] == [
        {
            'edge_id': 'right:output->left:input',
            'source_node': 'right',
            'target_node': 'left',
            'horizontal_distance': 200,
        }
    ]
    assert result['overlapping_nodes'] == [{'node_ids': ['left', 'overlap']}]
