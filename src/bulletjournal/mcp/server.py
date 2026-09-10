from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal
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
from bulletjournal.mcp.auth import is_controller_request, is_loopback_host, validate_local_request
from bulletjournal.mcp.errors import map_error, tool_error

McpGraphOperation = Annotated[
    AddNotebookNodeOperation
    | AddPipelineTemplateOperation
    | AddConstantNodeOperation
    | AddEdgeOperation
    | RemoveEdgeOperation,
    Field(discriminator='type'),
]
McpTemplateKind = Literal['notebook', 'pipeline']
McpProjectStateSection = Literal['summary', 'graph', 'validation', 'notices', 'recent_runs']
McpRunTarget = Literal['node', 'selection', 'all_stale']
McpRunMode = Literal['run_stale', 'run_all']
McpRunScope = Literal['node', 'ancestors', 'descendants']
McpRunAction = Literal['use_stale', 'run_upstream']
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
_IDEMPOTENT_MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


def create_mcp_app(container: Any, server_config: ServerConfig):
    token = mcp_bearer_token_from_env()
    if not is_loopback_host(server_config.host) and token is None and server_config.controller_token is None:
        raise ValueError('BULLETJOURNAL_MCP_TOKEN is required when MCP is exposed beyond loopback.')
    allowed_origins = {
        f'http://127.0.0.1:{server_config.port}',
        f'http://localhost:{server_config.port}',
    }
    if server_config.dev_frontend_url:
        parsed = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(server_config.dev_frontend_url)
        if parsed.scheme and parsed.netloc:
            allowed_origins.add(f'{parsed.scheme}://{parsed.netloc}')
    server = MCPServer('BulletJournal', version='2.2.7')

    def invoke(callback, *args, **kwargs):
        async def wrapped():
            try:
                return {'ok': True, 'result': await asyncio.to_thread(callback, *args, **kwargs)}
            except Exception as exc:  # Domain errors are returned as tool results.
                return map_error(exc)

        return wrapped()

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Discover active notebook and pipeline templates. Use a returned `ref` in other tools instead of '
            'guessing template names. `kind` is `notebook` or `pipeline`; `limit` is 1 through 100; '
            '`next_cursor` is supplied when another page is available.'
        ),
    )
    async def list_templates(
        kind: McpTemplateKind | None = None,
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
            'Get a template returned by `list_templates`. `include_interface` is supported only for notebook '
            'templates; `include_definition` is supported only for pipeline templates; `include_source` is '
            'supported for both kinds and can return substantial content.'
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
            '`notices`, and `recent_runs`. Omit `sections` to get summary, graph, validation, and notices. '
            'Read graph state before graph mutations: its `graph_version` is required by '
            '`apply_graph_changes`. Graph nodes include execution metadata when available.'
        ),
    )
    async def get_project_state(
        sections: list[McpProjectStateSection] | None = None,
        node_ids: list[str] | None = None,
        run_history_limit: Annotated[int, Field(ge=0, le=100)] = 20,
    ) -> dict[str, Any]:
        return await invoke(
            container.project_service.get_compact_state,
            sections=sections,
            node_ids=node_ids,
            run_history_limit=run_history_limit,
        )

    @server.tool(
        annotations=_READ_ONLY,
        description='Get one managed run by ID. Use after `start_run` or `wait_for_run` to inspect its latest status.',
    )
    async def get_run(run_id: str) -> dict[str, Any]:
        return await invoke(container.run_service.get_run, run_id)

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Wait up to 30 seconds for a managed run to reach a terminal state. Returns `completed=false` and '
            '`timed_out=true` when it is still running; call again or use `get_run` to continue monitoring.'
        ),
    )
    async def wait_for_run(run_id: str, timeout_seconds: Annotated[float, Field(ge=0, le=30)] = 30) -> dict[str, Any]:
        return await invoke(container.run_service.wait_for_run, run_id, timeout_seconds=timeout_seconds)

    @server.tool(
        annotations=_IDEMPOTENT_MUTATING,
        description=(
            'Apply graph changes using the supported operation schemas: `add_notebook_node`, '
            '`add_pipeline_template`, `add_constant_node`, `add_edge`, and `remove_edge`. '
            'Pipeline templates can create their complete graph in one `add_pipeline_template` operation. '
            'Read `get_project_state` first and pass its `graph_version`; use a new nonblank `request_id` for '
            'each logical mutation and reuse it only to retry that same mutation. Operations run in order.'
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

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Set the non-null value of a live constant node. The value must match the node data type; integers '
            'are accepted for `float`, and lists are accepted for `pandas.Series`. `file` and '
            '`pandas.DataFrame` values cannot be set through MCP. This changes an input and can stale '
            'downstream work.'
        ),
    )
    async def set_constant_value(node_id: str, value: Any) -> dict[str, Any]:
        return await invoke(container.artifact_service.set_constant_value, node_id, value)

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Start a noninteractive managed run. `target=node` requires `node_id` and supports `mode` '
            '(`run_stale` or `run_all`) and `scope` (`node`, `ancestors`, or `descendants`). '
            '`target=selection` requires `node_ids`; `target=all_stale` needs no node input. For blocked '
            'inputs, omit `action` first and inspect `confirmation_required` details; only then use the '
            'explicit user-approved action `use_stale` or `run_upstream`. `edit_run` is not available through MCP.'
        ),
    )
    async def start_run(
        target: McpRunTarget,
        node_id: str | None = None,
        node_ids: list[str] | None = None,
        mode: McpRunMode = 'run_stale',
        scope: McpRunScope = 'node',
        action: McpRunAction | None = None,
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

    @server.tool(
        annotations=_IDEMPOTENT_MUTATING,
        description=(
            'Request cancellation of the currently active matching run. Cancellation is asynchronous: '
            '`cancelling` means the request was accepted, while `not_running` means no matching active run. '
            'It is safe to retry this request.'
        ),
    )
    async def cancel_run(run_id: str) -> dict[str, Any]:
        return await invoke(container.run_service.cancel_run, run_id)

    @server.resource(
        'bulletjournal://project/summary',
        name='Project summary',
        description='Current compact project summary without filesystem paths or template source.',
        mime_type='application/json',
    )
    async def project_summary() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['summary'])

    @server.resource(
        'bulletjournal://project/graph',
        name='Project graph',
        description='Current compact graph snapshot. Read before graph mutations to obtain its graph version.',
        mime_type='application/json',
    )
    async def project_graph() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['graph'])

    @server.resource(
        'bulletjournal://project/validation',
        name='Project validation',
        description='Current validation findings for the project graph and nodes.',
        mime_type='application/json',
    )
    async def project_validation() -> dict[str, Any]:
        return await asyncio.to_thread(container.project_service.get_compact_state, sections=['validation'])

    @server.resource(
        'bulletjournal://templates/{ref}/documentation',
        name='Template documentation',
        description='Markdown documentation for one template. Percent-encode the template ref exactly once.',
        mime_type='text/markdown',
    )
    async def template_documentation(ref: str) -> str:
        template = await asyncio.to_thread(container.template_service.get_template, unquote(ref))
        return str(template.get('documentation') or '')

    @server.resource(
        'bulletjournal://templates/{ref}/interface',
        name='Template interface',
        description='Parsed input/output interface for one notebook template. Percent-encode the ref exactly once.',
        mime_type='application/json',
    )
    async def template_interface(ref: str) -> dict[str, Any]:
        return await asyncio.to_thread(container.template_service.get_template, unquote(ref), include_interface=True)

    app = server.streamable_http_app(
        streamable_http_path='/', json_response=True, host=server_config.host, transport_security=None
    )

    async def local_mcp_auth(request: Request, call_next):
        if not is_controller_request(request, controller_token=server_config.controller_token):
            validate_local_request(request, token=token, allowed_origins=allowed_origins)
        container.project_service.begin_mcp_activity()
        try:
            return await call_next(request)
        finally:
            container.project_service.end_mcp_activity()

    app.add_middleware(BaseHTTPMiddleware, dispatch=local_mcp_auth)
    return app
