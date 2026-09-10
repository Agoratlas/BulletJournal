from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from bulletjournal.api.app import create_app
from bulletjournal.config import ServerConfig, mcp_enabled_from_env
from bulletjournal.mcp.server import McpGraphOperation
from bulletjournal.storage.project_fs import init_project_root


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


def test_mcp_graph_operation_schema_accepts_only_supported_rest_operations() -> None:
    adapter = TypeAdapter(McpGraphOperation)

    pipeline = adapter.validate_python({'type': 'add_pipeline_template', 'template_ref': 'provider/pipeline'})

    assert pipeline.template_ref == 'provider/pipeline'
    with pytest.raises(ValidationError):
        adapter.validate_python({'type': 'add_area_node', 'node_id': 'area', 'title': 'Area'})
