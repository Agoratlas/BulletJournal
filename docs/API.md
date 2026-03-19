# API

Base path: `/api/v1`

BulletJournal serves one project per process. Runtime routes are single-project routes and do not include `project_id` path parameters.

## Core endpoints

- `GET /project`
- `GET /project/snapshot`
- `GET /project/status`
- `GET /graph`
- `PATCH /graph`
- `GET /nodes/{node_id}`
- `POST /nodes/{node_id}/run`
- `POST /runs/run-all`
- `POST /runs/{run_id}/cancel`
- `GET /artifacts`
- `GET /artifacts/{node_id}/{artifact_name}`
- `POST /file-inputs/{node_id}/upload`
- `GET /checkpoints`
- `POST /checkpoints`
- `POST /checkpoints/{checkpoint_id}/restore`
- `GET /templates`
- `GET /events`
- `GET /controller/status`
- `POST /controller/mark-environment-changed`

## Removed endpoints

- `POST /api/v1/projects/init`
- `POST /api/v1/projects/open`
- `GET /api/v1/projects/current`
- any `/api/v1/projects/{project_id}/...` runtime route

## Controller endpoints

If `BULLETJOURNAL_CONTROLLER_TOKEN` is set, all `/controller/*` endpoints require `Authorization: Bearer <token>`.

`POST /controller/mark-environment-changed` body:

```json
{
  "reason": "requirements updated by controller",
  "mark_all_artifacts_stale": true
}
```

## SSE

`GET /events` returns `text/event-stream`.

- supports `Last-Event-ID` header and `last_event_id` query parameter
- emits keepalives and `stream.reset`
- carries project-scoped events for the single bound project
