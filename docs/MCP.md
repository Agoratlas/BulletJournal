# MCP

BulletJournal exposes a compact Model Context Protocol (MCP) interface for one
project. An MCP client is the complete agent-facing interface: agents should use
tool and resource discovery rather than rely on filesystem access or guessed IDs.

## Connection and safety

Set `BULLETJOURNAL_ENABLE_MCP` to `1`, `true`, `yes`, or `on` to enable the
endpoint. `0`, `false`, `no`, and `off` disable it; other nonempty values fail
startup. The default local endpoint is `http://127.0.0.1:8765/mcp`. With
`--base-path /p/<project-id>`, it is `/p/<project-id>/mcp`.

The server validates browser Origins and permits no-Origin local MCP clients. A
non-loopback server requires `BULLETJOURNAL_MCP_TOKEN` as a bearer token. A
Controller-managed project uses its configured remote MCP URL and OAuth instead.

The server does not expose project paths, full snapshots, or arbitrary
pickle-backed artifact data. Template source and pipeline definitions are opt-in.

## Operating workflow

1. Discover tools and resources, then read the project graph and validation.
2. Discover templates with `list_templates`; pass returned `ref` values to other
   calls rather than guessing names.
3. Before `apply_graph_changes`, read the graph's `graph_version`. Send that
   version plus a new nonblank `request_id` for one logical mutation.
4. If a write returns `graph_version_conflict`, reread state and decide whether
   to recreate the intended change. Reuse a request ID only to retry the exact
   same request.
5. When a run returns `confirmation_required`, inspect its details and obtain a
   user decision before sending `use_stale` or `run_upstream`. Never guess this.
6. After starting a run, use `get_run` or `wait_for_run` to observe its outcome.

## Tool reference

All successful tools return `{"ok": true, "result": ...}`. Expected domain
failures return `{"ok": false, "error": {"code", "message", "retryable",
"details"}}` as a tool result.

### `list_templates`

Lists active templates. Inputs are `kind` (`notebook` or `pipeline`), exact
`provider`, text `query`, `hidden` (default `false`), pagination `cursor`, and
`limit` (default `50`, range `1..100`). Returns `items` and `next_cursor`.
Each item includes a reusable `ref`, kind, title, documentation, and source hash.

### `get_template`

Gets a template by a discovered `ref`. `include_source` applies to either kind.
`include_interface` is notebook-only. `include_definition` is pipeline-only.

### `get_project_state`

Gets compact state and always returns `graph_version`. `sections` accepts only
`summary`, `graph`, `validation`, `notices`, and `recent_runs`; omitted sections
default to summary, graph, validation, and notices. Use `node_ids` to restrict
graph-oriented results to existing nodes. `run_history_limit` defaults to `20`
and ranges from `0..100`.

### `get_run` and `wait_for_run`

`get_run` gets a run by `run_id`. `wait_for_run` polls the same run for up to
`timeout_seconds` (default and maximum `30`, minimum `0`) and returns `run`,
`completed`, and `timed_out`. A timeout is not a failed run.

### `apply_graph_changes`

Applies a nonempty ordered batch atomically. It requires the current
`expected_graph_version`, a nonblank idempotency `request_id`, and `operations`.
Only these operation types are accepted; unknown fields are rejected.

| Type | Required fields | Optional fields | Notes |
| --- | --- | --- | --- |
| `add_notebook_node` | `node_id`, `title` | `x`, `y`, `w`, `h`, `template_ref`, `source_text`, `ui` | `template_ref` must be an active notebook template. Inline `source_text` takes precedence. Without either, uses `builtin/empty_notebook`. |
| `add_pipeline_template` | `template_ref` | `x`, `y`, `node_id_suffix` | Template must be an active pipeline. Creates its complete graph. Use a suffix to avoid node-ID collisions. |
| `add_constant_node` | `node_id`, `data_type` | `title`, layout, `value`, `value_json`, `ui` | Data type must be nonblank. Initial values follow constant-value type rules. |
| `add_edge` | `source_node`, `source_port`, `target_node`, `target_port` | None | Ports must exist, have exactly matching types, and preserve an acyclic graph. |
| `remove_edge` | `edge_id` | None | Missing edges are a successful no-op. Edge IDs are `{source_node}.{source_port}__{target_node}.{target_port}`. |

Operations execute in supplied order, so a batch can create nodes before adding
edges. A graph edit can invalidate downstream work and interrupt an affected run.

### `set_constant_value`

Sets a non-null value for a live constant `node_id`. The value must match that
constant's declared data type. Integers are accepted for `float`; lists are
accepted for `pandas.Series`. `file` and `pandas.DataFrame` values cannot be set
through MCP. A constant update may stale downstream outputs.

### `start_run`

Starts a noninteractive managed run. `target` is exactly one of:

| Target | Required input | Applicable controls |
| --- | --- | --- |
| `node` | `node_id` | `mode`: `run_stale` or `run_all`; `scope`: `node`, `ancestors`, or `descendants` |
| `selection` | `node_ids` | `action` only; the run uses stale mode |
| `all_stale` | None | No node input; the run uses stale mode |

`action` is omitted for the initial request, or is `use_stale` or `run_upstream`
after a user-approved confirmation. `edit_run` is deliberately unavailable. A
successful start returns a running run ID; a `noop` result means no runnable work.

### `cancel_run`

Requests cancellation for the active matching `run_id`. It returns `cancelling`
when accepted or `not_running` when no matching run is active. Cancellation is
asynchronous and this call is safe to retry.

## Resources

| URI | Contents |
| --- | --- |
| `bulletjournal://project/summary` | Compact project summary. |
| `bulletjournal://project/graph` | Current compact graph snapshot; read before graph writes. |
| `bulletjournal://project/validation` | Current graph and node validation findings. |
| `bulletjournal://templates/{ref}/documentation` | Markdown documentation for one template. |
| `bulletjournal://templates/{ref}/interface` | Parsed interface for one notebook template. |

For template resource URIs, percent-encode `ref` exactly once. Resources are
current snapshots, not subscriptions; read them again when freshness matters.

## Error recovery

| Code | Meaning and agent action |
| --- | --- |
| `invalid_argument` | Correct the documented input, range, or conditional requirement. |
| `not_found` | Rediscover the referenced template, node, or run. |
| `graph_version_conflict` | Retryable. Reread project state and construct a new mutation if still desired. |
| `validation_failed` | Graph, template, port, type, or cycle validation failed. Read validation and correct the planned graph. |
| `frozen_block` | The change would affect a frozen block; do not bypass the restriction. |
| `run_conflict` | Retryable. Another managed run is active; wait for it or ask the user. |
| `confirmation_required` | Inspect details and obtain a user decision before retrying with an action. |
| `run_blocked` | Required inputs remain unavailable; inspect details and repair the project state. |
| `internal_error` | The operation could not complete. Preserve the error details for the user rather than blindly retrying a write. |
