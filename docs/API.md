# API

Base path: `/api/v1`

## Core endpoints

- `POST /projects/init`
- `POST /projects/open`
- `GET /projects/current`
- `GET /projects/{project_id}/snapshot`
- `GET /projects/{project_id}/graph`
- `PATCH /projects/{project_id}/graph`
- `GET /projects/{project_id}/nodes/{node_id}`
- `POST /projects/{project_id}/nodes/{node_id}/run`
- `POST /projects/{project_id}/runs/run-all`
- `POST /projects/{project_id}/runs/{run_id}/cancel`
- `GET /projects/{project_id}/artifacts`
- `GET /projects/{project_id}/artifacts/{node_id}/{artifact_name}`
- `POST /projects/{project_id}/file-inputs/{node_id}/upload`
- `GET /projects/{project_id}/checkpoints`
- `POST /projects/{project_id}/checkpoints`
- `POST /projects/{project_id}/checkpoints/{checkpoint_id}/restore`
- `GET /projects/{project_id}/templates`
- `GET /projects/{project_id}/events`

## Request contracts

`PATCH /projects/{project_id}/graph` requires:

- `graph_version: int`
- `operations: []` where each item is one of:
  - `add_notebook_node { node_id, title, x?, y?, w?, h?, template_ref? }`
  - `add_file_input_node { node_id, title, x?, y?, w?, h? }`
  - `add_pipeline_template { template_ref, x?, y?, node_id_prefix? }`
  - `add_edge { source_node, source_port, target_node, target_port }`
  - `remove_edge { edge_id }`
  - `update_node_layout { node_id, x, y, w, h }`
  - `update_node_title { node_id, title }`
  - `update_node_hidden_inputs { node_id, hidden_inputs }`
  - `delete_node { node_id }`

Unknown fields are rejected with `422`.

`POST /projects/{project_id}/nodes/{node_id}/run` requires:

- `mode`: `run_stale`, `run_all`, or `edit_run`
- `action`: `use_stale` or `run_upstream` for managed runs, omitted for `edit_run`

`POST /projects/{project_id}/runs/run-all` currently accepts only:

- `mode: run_stale`

## Response semantics

- `409` is used for graph conflicts and run conflicts
- `404` is used for unknown projects, nodes, artifacts, and checkpoints
- `400` is used for invalid requests such as uploading to a non-file-input node
- `422` is used when request payloads fail schema validation

Managed run responses can return:

- `{"status": "succeeded", ...}` when the plan completes
- `{"status": "failed", ...}` when a node run fails
- `{"status": "cancelled", ...}` when a run is cancelled
- `{"status": "noop", ...}` when `run_stale` finds nothing to refresh
- `{"status": "blocked", "blocked_inputs": [...]}` when pending inputs cannot be resolved safely
- `{"requires_confirmation": true, "blocked_inputs": [...]}` when the caller must choose an action

## SSE

`GET /projects/{project_id}/events` returns `text/event-stream`.

- supports `Last-Event-ID` header and `last_event_id` query parameter
- emits `retry:` hints and comment keepalives
- emits `stream.reset` when the in-memory event buffer has rotated past the requested cursor
- events are project-scoped; other-project events are filtered out before delivery

SSE event payload shape:

```json
{
  "id": 12,
  "event_type": "run.started",
  "project_id": "demo-project",
  "graph_version": 4,
  "timestamp": "2026-03-13T12:00:00Z",
  "payload": {
    "run_id": "...",
    "node_ids": ["node_a"]
  }
}
```

## Event types

- `project.opened`
- `graph.updated`
- `notebook.reparsed`
- `validation.updated`
- `artifact.state_changed`
- `run.queued`
- `run.started`
- `run.progress`
- `run.failed`
- `run.finished`
- `checkpoint.created`
- `checkpoint.restored`
