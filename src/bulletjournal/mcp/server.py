from __future__ import annotations

import asyncio
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Request
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware

from bulletjournal.api.schemas import (
    AddConstantNodeOperation,
    AddEdgeOperation,
    AddNotebookNodeOperation,
    AddPipelineTemplateOperation,
    RemoveEdgeOperation,
)
from bulletjournal.config import ServerConfig, mcp_bearer_token_from_env
from bulletjournal.mcp.auth import is_loopback_host, validate_local_request
from bulletjournal.mcp.errors import map_error, tool_error

McpGraphOperation = Annotated[
    AddNotebookNodeOperation
    | AddPipelineTemplateOperation
    | AddConstantNodeOperation
    | AddEdgeOperation
    | RemoveEdgeOperation,
    Field(discriminator='type'),
]
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
_IDEMPOTENT_MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


def create_mcp_app(container: Any, server_config: ServerConfig):
    token = mcp_bearer_token_from_env()
    if not is_loopback_host(server_config.host) and token is None:
        raise ValueError('BULLETJOURNAL_MCP_TOKEN is required when MCP is exposed beyond loopback.')
    allowed_origins = {
        f'http://127.0.0.1:{server_config.port}',
        f'http://localhost:{server_config.port}',
    }
    if server_config.dev_frontend_url:
        parsed = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(server_config.dev_frontend_url)
        if parsed.scheme and parsed.netloc:
            allowed_origins.add(f'{parsed.scheme}://{parsed.netloc}')
    server = MCPServer('BulletJournal', version='2.2.6')

    def invoke(callback, *args, **kwargs):
        async def wrapped():
            try:
                return {'ok': True, 'result': await asyncio.to_thread(callback, *args, **kwargs)}
            except Exception as exc:  # Domain errors are returned as tool results.
                return map_error(exc)

        return wrapped()

    @server.tool(annotations=_READ_ONLY)
    async def list_templates(
        kind: str | None = None,
        provider: str | None = None,
        query: str | None = None,
        hidden: bool = False,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return await invoke(
            container.template_service.list_template_catalog,
            kind=kind,
            provider=provider,
            query=query,
            include_hidden=hidden,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Get a template. `include_interface` is supported only for notebook templates; '
            '`include_definition` is supported only for pipeline templates. '
            '`include_source` is supported for both kinds.'
        ),
    )
    async def get_template(
        ref: str, include_source: bool = False, include_interface: bool = False, include_definition: bool = False
    ) -> dict[str, Any]:
        return await invoke(
            container.template_service.get_template,
            ref,
            include_source=include_source,
            include_interface=include_interface,
            include_definition=include_definition,
        )

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Get compact project state. Valid sections are `summary`, `graph`, `validation`, '
            '`notices`, and `recent_runs`. Graph nodes include execution metadata when available.'
        ),
    )
    async def get_project_state(
        sections: list[str] | None = None, node_ids: list[str] | None = None, run_history_limit: int = 20
    ) -> dict[str, Any]:
        return await invoke(
            container.project_service.get_compact_state,
            sections=sections,
            node_ids=node_ids,
            run_history_limit=run_history_limit,
        )

    @server.tool(annotations=_READ_ONLY)
    async def get_run(run_id: str) -> dict[str, Any]:
        return await invoke(container.run_service.get_run, run_id)

    @server.tool(annotations=_READ_ONLY)
    async def wait_for_run(run_id: str, timeout_seconds: float = 30) -> dict[str, Any]:
        return await invoke(container.run_service.wait_for_run, run_id, timeout_seconds=timeout_seconds)

    @server.tool(
        annotations=_IDEMPOTENT_MUTATING,
        description=(
            'Apply graph changes using the supported operation schemas: `add_notebook_node`, '
            '`add_pipeline_template`, `add_constant_node`, `add_edge`, and `remove_edge`. '
            'Pipeline templates can create their complete graph in one `add_pipeline_template` operation.'
        ),
    )
    async def apply_graph_changes(
        expected_graph_version: int, request_id: str, operations: list[McpGraphOperation]
    ) -> dict[str, Any]:
        if not request_id.strip():
            return tool_error('invalid_argument', 'request_id is required.')
        if not operations:
            return tool_error('invalid_argument', 'At least one graph operation is required.')
        return await invoke(
            container.graph_service.apply_operations,
            expected_graph_version,
            [operation.model_dump(mode='python') for operation in operations],
            request_id=request_id,
        )

    @server.tool(annotations=_MUTATING)
    async def set_constant_value(node_id: str, value: Any) -> dict[str, Any]:
        return await invoke(container.artifact_service.set_constant_value, node_id, value)

    @server.tool(annotations=_MUTATING)
    async def start_run(
        target: str,
        node_id: str | None = None,
        node_ids: list[str] | None = None,
        mode: str = 'run_stale',
        scope: str = 'node',
        action: str | None = None,
    ) -> dict[str, Any]:
        if mode == 'edit_run':
            return tool_error('invalid_argument', 'edit_run is not supported by MCP.')
        try:
            if target == 'node' and node_id:
                response = await asyncio.to_thread(
                    container.run_service.start_node_run, node_id, mode=mode, scope=scope, action=action
                )
            elif target == 'selection' and node_ids:
                response = await asyncio.to_thread(container.run_service.start_selection_run, node_ids, action=action)
            elif target == 'all_stale':
                response = await asyncio.to_thread(container.run_service.run_all_stale)
            else:
                return tool_error('invalid_argument', 'Specify a valid target and its node input.')
            if response.get('requires_confirmation'):
                return tool_error('confirmation_required', 'Run confirmation is required.', details=response)
            if response.get('status') == 'blocked':
                return tool_error('run_blocked', 'The run is blocked.', details=response)
            return {'ok': True, 'result': response}
        except Exception as exc:
            return map_error(exc)

    @server.tool(annotations=_IDEMPOTENT_MUTATING)
    async def cancel_run(run_id: str) -> dict[str, Any]:
        return await invoke(container.run_service.cancel_run, run_id)

    @server.resource('bulletjournal://project/summary', mime_type='application/json')
    async def project_summary() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['summary'])

    @server.resource('bulletjournal://project/graph', mime_type='application/json')
    async def project_graph() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['graph'])

    @server.resource('bulletjournal://project/validation', mime_type='application/json')
    async def project_validation() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['validation'])

    @server.resource('bulletjournal://templates/{ref}/documentation', mime_type='text/markdown')
    async def template_documentation(ref: str) -> str:
        template = await asyncio.to_thread(container.template_service.get_template, unquote(ref))
        return str(template.get('documentation') or '')

    @server.resource('bulletjournal://templates/{ref}/interface', mime_type='application/json')
    async def template_interface(ref: str) -> dict[str, Any]:
        return await asyncio.to_thread(container.template_service.get_template, unquote(ref), include_interface=True)

    app = server.streamable_http_app(
        streamable_http_path='/', json_response=True, host=server_config.host, transport_security=None
    )

    async def local_mcp_auth(request: Request, call_next):
        validate_local_request(request, token=token, allowed_origins=allowed_origins)
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=local_mcp_auth)
    return app
