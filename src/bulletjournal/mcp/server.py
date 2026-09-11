from __future__ import annotations

import asyncio
import math
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import Request
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware

from bulletjournal.api.schemas import (
    AddAreaNodeOperation,
    AddConstantNodeOperation,
    AddDashboardNodeOperation,
    AddEdgeOperation,
    AddNotebookNodeOperation,
    AddOrganizerNodeOperation,
    AddPipelineTemplateOperation,
    DashboardPanelInput,
    DashboardSourceInput,
    DeleteNodeOperation,
    RemoveEdgeOperation,
    RenameNodeOperation,
    UpdateAreaStyleOperation,
    UpdateConstantNodeOperation,
    UpdateNodeFrozenOperation,
    UpdateNodeLayoutOperation,
    UpdateNodeTitleOperation,
    UpdateOrganizerPortsOperation,
)
from bulletjournal.config import ServerConfig, mcp_bearer_token_from_env
from bulletjournal.mcp.auth import is_controller_request, is_loopback_host, validate_local_request
from bulletjournal.mcp.errors import map_error, tool_error
from bulletjournal.services.graph_service import GRID_SIZE

McpGraphOperation = Annotated[
    AddNotebookNodeOperation
    | AddPipelineTemplateOperation
    | AddConstantNodeOperation
    | AddOrganizerNodeOperation
    | AddAreaNodeOperation
    | AddDashboardNodeOperation
    | AddEdgeOperation
    | RemoveEdgeOperation
    | UpdateNodeLayoutOperation
    | UpdateNodeTitleOperation
    | RenameNodeOperation
    | UpdateConstantNodeOperation
    | UpdateOrganizerPortsOperation
    | UpdateAreaStyleOperation
    | DeleteNodeOperation
    | UpdateNodeFrozenOperation,
    Field(discriminator='type'),
]
McpTemplateKind = Literal['notebook', 'pipeline']
McpProjectStateSection = Literal['summary', 'graph', 'validation', 'notices', 'recent_runs']
McpRunTarget = Literal['node', 'selection', 'all_stale']
McpRunMode = Literal['run_stale', 'run_all']
McpRunScope = Literal['node', 'ancestors', 'descendants']
McpRunAction = Literal['use_stale', 'run_upstream']
McpLogStream = Literal['stdout', 'stderr']
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
        description=(
            'Analyze the current graph layout for potential improvements. Returns up to `limit` longest edges, '
            'edges whose source is to the right of its target, and pairs of overlapping non-area nodes. Edge '
            'length is the Euclidean distance between node centers; touching node boundaries do not count as an '
            'overlap. Results are suggestions only and do not modify the graph.'
        ),
    )
    async def layout_helper(limit: Annotated[int, Field(ge=1, le=100)] = 10) -> dict[str, Any]:
        return await invoke(_layout_helper, container.graph_service, limit=limit)

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
            'Apply an atomic ordered graph-edit batch. Read `get_project_state` first and use its current '
            '`graph_version` as `expected_graph_version`. Supported operations are: create `add_notebook_node`, '
            '`add_constant_node`, `add_organizer_node`, `add_area_node`, or '
            '`add_dashboard_node`; instantiate `add_pipeline_template`; connect `add_edge` or disconnect '
            '`remove_edge`; move `update_node_layout` (x/y required); resize only an area with '
            '`update_node_layout` (include w and/or h; omit w/h to retain size); '
            'change a title with `update_node_title`; change an ID and title together with `rename_node`; '
            'update `update_organizer_ports`, `update_area_style`, `update_constant_node`, or '
            '`update_node_frozen`; and remove a block with `delete_node`. `delete_node` creates a restorable '
            'tombstone. Pipeline templates can create their complete graph in one operation. '
            'Read `get_project_state` first and pass its `graph_version`; use a new nonblank `request_id` for '
            'each logical mutation and reuse it only to retry that same mutation. Operations run in order. '
            'Organizer ports are objects with nonblank `key`, `name`, and `data_type`. Area `title_position` '
            'is one of `top-left`, `top-center`, `top-right`, `right-center`, `bottom-right`, `bottom-center`, '
            '`bottom-left`, or `left-center`; `color` is `red`, `orange`, `yellow`, `green`, `blue`, `purple`, '
            '`white`, or `black`; `filled` is boolean.'
        ),
    )
    async def apply_graph_changes(
        expected_graph_version: int, request_id: str, operations: list[McpGraphOperation]
    ) -> dict[str, Any]:
        if not request_id.strip():
            return tool_error('invalid_argument', 'request_id is required.')
        if not operations:
            return tool_error('invalid_argument', 'At least one graph operation is required.')
        resize_error = _validate_area_only_resizes(container.graph_service, operations)
        if resize_error is not None:
            return tool_error('invalid_argument', resize_error)
        return await invoke(
            container.graph_service.apply_operations,
            expected_graph_version,
            [operation.model_dump(mode='python') for operation in operations],
            request_id=request_id,
        )

    @server.tool(
        annotations=_IDEMPOTENT_MUTATING,
        description=(
            'Move every node entirely within the inclusive rectangle `x_min`, `x_max`, `y_min`, `y_max` by the '
            'relative offset `dx`, `dy`. Node bounds include x, y, width, and height; intersecting nodes are not '
            'selected. The offsets must be aligned to the 20px grid. Read '
            '`get_project_state` first and pass its current `graph_version`; use a new nonblank `request_id` for '
            'each logical mutation and reuse it only to retry that same mutation.'
        ),
    )
    async def move_nodes_in_rectangle(
        expected_graph_version: int,
        request_id: str,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        dx: int,
        dy: int,
    ) -> dict[str, Any]:
        if not request_id.strip():
            return tool_error('invalid_argument', 'request_id is required.')
        return await invoke(
            _move_nodes_in_rectangle,
            container.graph_service,
            expected_graph_version=expected_graph_version,
            request_id=request_id,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            dx=dx,
            dy=dy,
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

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Read UTF-8 Python source for one notebook block. `offset` is a zero-based physical-line offset; '
            'omit `limit` to return all remaining lines, or set it from 1 through 100. The result includes '
            '`total_lines`, `returned_lines`, and `next_offset` for pagination. `node_id` must name an existing '
            'notebook, not a constant, organizer, area, dashboard, or file-input block.'
        ),
    )
    async def get_notebook_source(
        node_id: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int | None, Field(ge=1, le=100)] = None,
    ) -> dict[str, Any]:
        return await invoke(container.notebook_service.get_notebook_source, node_id, offset=offset, limit=limit)

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Replace the complete source of an existing notebook block. This is full-document replacement, not '
            'a partial update: use `patch_notebook_source` to replace only selected lines. The source is saved '
            'even when parsing reports validation errors; '
            'the result returns the parsed interface, and project validation contains those errors. A valid '
            'interface change can remove incompatible edges, stale downstream work, and interrupt affected runs.'
        ),
    )
    async def update_notebook_source(node_id: str, source_text: str) -> dict[str, Any]:
        return await invoke(container.notebook_service.update_notebook_source, node_id, source_text)

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Replace an inclusive range of existing notebook source lines without sending the whole document. '
            '`start_line` and `end_line` are one-based line numbers; line 1 is the first line. `replacement` '
            'replaces every line from start through end and may contain zero, one, or many lines. Use an empty '
            'string to delete the selected lines. Read the relevant range with `get_notebook_source` first; its '
            'zero-based `offset` is different from these one-based line numbers. The patch reparses the notebook '
            'and can change ports, remove incompatible edges, stale downstream work, or interrupt affected runs.'
        ),
    )
    async def patch_notebook_source(
        node_id: str,
        start_line: Annotated[int, Field(ge=1)],
        end_line: Annotated[int, Field(ge=1)],
        replacement: str,
    ) -> dict[str, Any]:
        return await invoke(
            container.notebook_service.patch_notebook_source,
            node_id,
            start_line=start_line,
            end_line=end_line,
            replacement=replacement,
        )

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Read the latest managed execution log for a node. Omit `stream` to get stdout/stderr summaries. '
            'Set `stream` to exactly `stdout` or `stderr` to get that stream text and truncation metadata. '
            'Only the latest managed execution for the node is available; logs are not a historical run archive.'
        ),
    )
    async def get_execution_logs(node_id: str, stream: McpLogStream | None = None) -> dict[str, Any]:
        if stream is None:
            return await invoke(container.artifact_service.get_execution_logs, node_id)
        return await invoke(container.artifact_service.get_execution_log, node_id, stream)

    @server.tool(
        annotations=_READ_ONLY,
        description=(
            'Read a dashboard document, including its required `version`. Read it before `update_dashboard`; '
            'that tool requires the exact current version to prevent overwriting another edit.'
        ),
    )
    async def get_dashboard(dashboard_id: str) -> dict[str, Any]:
        return await invoke(container.dashboard_service.get_dashboard, dashboard_id)

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Create a dashboard block and its dashboard document. `sources` is a list of unique notebook '
            'objects `{node_id}`. Each `panels` object needs `node_id` and `asset_name`, and may set '
            '`panel_id`, `visible`, `position`, `panel_height`, `modifier_overrides`, and '
            '`override_schema_hash`. Every panel node must be in `sources`; positions are normalized. '
            '`dashboard_id` is optional and otherwise derived from the title.'
        ),
    )
    async def create_dashboard(
        title: str,
        sources: list[DashboardSourceInput],
        panels: list[DashboardPanelInput],
        dashboard_id: str | None = None,
        x: int = 80,
        y: int = 80,
    ) -> dict[str, Any]:
        return await invoke(
            container.dashboard_service.create_dashboard,
            dashboard_id=dashboard_id,
            title=title,
            sources=[source.model_dump(mode='python') for source in sources],
            panels=[panel.model_dump(mode='python') for panel in panels],
            x=x,
            y=y,
        )

    @server.tool(
        annotations=_MUTATING,
        description=(
            'Update an existing dashboard document. First call `get_dashboard`, then pass its exact `version` '
            'as `dashboard_version`. Supply only fields to replace: `title`, complete `sources`, and/or complete '
            '`panels`; omitted fields are unchanged. Sources and panels follow `create_dashboard` rules. On a '
            'version conflict, reread the dashboard and construct a new desired update.'
        ),
    )
    async def update_dashboard(
        dashboard_id: str,
        dashboard_version: int,
        title: str | None = None,
        sources: list[DashboardSourceInput] | None = None,
        panels: list[DashboardPanelInput] | None = None,
    ) -> dict[str, Any]:
        return await invoke(
            container.dashboard_service.patch_dashboard,
            dashboard_id,
            dashboard_version=dashboard_version,
            title=title,
            sources=None if sources is None else [source.model_dump(mode='python') for source in sources],
            panels=None if panels is None else [panel.model_dump(mode='python') for panel in panels],
        )

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

    @server.resource(
        'bulletjournal://nodes/{node_id}/source',
        name='Notebook source',
        description='Complete UTF-8 source for one notebook block. This resource rejects non-notebook node IDs.',
        mime_type='text/x-python',
    )
    async def notebook_source(node_id: str) -> str:
        result = await asyncio.to_thread(container.notebook_service.get_notebook_source, unquote(node_id))
        return str(result['source_text'])

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


def _validate_area_only_resizes(graph_service: Any, operations: list[McpGraphOperation]) -> str | None:
    graph = graph_service.project_service.graph()
    node_kinds = {node.id: node.kind.value for node in graph.nodes}
    for operation in operations:
        payload = operation.model_dump(mode='python')
        if payload['type'] == 'add_area_node':
            node_kinds[payload['node_id']] = 'area'
        elif payload['type'] == 'rename_node':
            old_node_id = payload['node_id']
            node_kinds[payload['new_node_id']] = node_kinds.pop(old_node_id, '')
        elif payload['type'] == 'delete_node':
            node_kinds.pop(payload['node_id'], None)
        elif payload['type'] == 'update_node_layout' and (payload['w'] is not None or payload['h'] is not None):
            if node_kinds.get(payload['node_id']) != 'area':
                return 'Only area blocks can be resized. Omit `w` and `h` to move another block.'
    return None


def _layout_helper(graph_service: Any, *, limit: int) -> dict[str, list[dict[str, Any]]]:
    graph = graph_service.project_service.graph()
    layouts = {entry.node_id: entry for entry in graph.layout}
    edge_details: list[dict[str, Any]] = []
    right_to_left_edges: list[dict[str, Any]] = []
    for edge in graph.edges:
        source = layouts.get(edge.source_node)
        target = layouts.get(edge.target_node)
        if source is None or target is None:
            continue
        source_center_x = source.x + source.w / 2
        source_center_y = source.y + source.h / 2
        target_center_x = target.x + target.w / 2
        target_center_y = target.y + target.h / 2
        detail = {
            'edge_id': edge.id,
            'source_node': edge.source_node,
            'target_node': edge.target_node,
            'length': math.hypot(target_center_x - source_center_x, target_center_y - source_center_y),
        }
        edge_details.append(detail)
        if source_center_x > target_center_x:
            right_to_left_edges.append(
                {
                    'edge_id': edge.id,
                    'source_node': edge.source_node,
                    'target_node': edge.target_node,
                    'horizontal_distance': source_center_x - target_center_x,
                }
            )
    node_kinds = {node.id: node.kind.value for node in graph.nodes}
    non_area_layouts = sorted(
        (entry for entry in graph.layout if node_kinds.get(entry.node_id) != 'area'), key=lambda entry: entry.node_id
    )
    overlaps = [
        {'node_ids': [first.node_id, second.node_id]}
        for index, first in enumerate(non_area_layouts)
        for second in non_area_layouts[index + 1 :]
        if _layouts_overlap(first, second)
    ]
    return {
        'longest_edges': sorted(edge_details, key=lambda edge: (-edge['length'], edge['edge_id']))[:limit],
        'right_to_left_edges': sorted(
            right_to_left_edges, key=lambda edge: (-edge['horizontal_distance'], edge['edge_id'])
        )[:limit],
        'overlapping_nodes': overlaps[:limit],
    }


def _layouts_overlap(first: Any, second: Any) -> bool:
    return (
        first.x < second.x + second.w
        and second.x < first.x + first.w
        and first.y < second.y + second.h
        and second.y < first.y + first.h
    )


def _move_nodes_in_rectangle(
    graph_service: Any,
    *,
    expected_graph_version: int,
    request_id: str,
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
    dx: int,
    dy: int,
) -> dict[str, Any]:
    values = {'dx': dx, 'dy': dy}
    unaligned = [name for name, value in values.items() if value % GRID_SIZE]
    if unaligned:
        raise ValueError(f'{", ".join(unaligned)} must be aligned to the {GRID_SIZE}px grid.')
    if x_min > x_max or y_min > y_max:
        raise ValueError('Rectangle minimum bounds must not exceed maximum bounds.')
    operations = [
        {'type': 'update_node_layout', 'node_id': entry.node_id, 'x': entry.x + dx, 'y': entry.y + dy}
        for entry in graph_service.project_service.graph().layout
        if x_min <= entry.x and entry.x + entry.w <= x_max and y_min <= entry.y and entry.y + entry.h <= y_max
    ]
    if not operations:
        return graph_service.get_graph()
    return graph_service.apply_operations(expected_graph_version, operations, request_id=request_id)
