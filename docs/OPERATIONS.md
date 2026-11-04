# Operations

## Start locally

```bash
bulletjournal start .
```

`bulletjournal start` fails fast unless the path is already a valid BulletJournal project root.

## Initialize project layout

```bash
bulletjournal init my-project
bulletjournal init /project --project-id study-a --skip-environment
```

Use `--skip-environment` when a controller or external automation owns `pyproject.toml`, `uv.lock`, and dependency installation. In that mode, BulletJournal creates or repairs only BulletJournal-owned project files and runtime directories.

## Development mode

```bash
bulletjournal dev . --open
```

## Health check

```bash
bulletjournal doctor .
```

## Rebuild derived state

```bash
bulletjournal rebuild-state .
```

This reparses notebooks and rebuilds derived interface/validation state from the project root.

## Environment changes

When dependencies change outside the app, mark notebook outputs stale offline:

```bash
bulletjournal mark-environment-changed . --reason "dependencies updated"
```

## Import and export

```bash
bulletjournal export . my-project.zip
bulletjournal import my-project.zip restored-project
```

Exports omit local virtual environments and other transient runtime files.

Use `--mode` to select `code_only`, `code_and_constants`, `code_and_data`,
`snapshot`, or `full` (the default). All modes produce directly importable project roots.

```bash
bulletjournal export . my-project-snapshot.zip --mode snapshot
```

A snapshot preserves current notebook code, graph/layout, dashboards,
`metadata/project.json`, `pyproject.toml`, and `uv.lock`. Its trimmed `metadata/state.db`
contains live node identities, current artifact/asset heads and their versions,
asset definitions/object links, annotations/favorites, execution/freshness heads,
current validation state (including dismissals), and the latest notebook status/error
summaries. Only object blobs referenced by current artifact or asset versions are included,
including stale outputs and constant values. Current-version publication/input
metadata is retained for provenance; historical input object blobs are not.

Snapshots exclude historical versions/runs, checkpoints, deleted-node recovery,
unreferenced objects, mutation caches, temporary files, and execution logs. Log
paths are cleared. Referenced objects are checksum/size validated, and missing or
corrupt objects fail the export. Capture uses the project lock to coordinate with
object collection and output publication. A snapshot restores saved
state; it does not capture a running Python process or external service state.

## Reverse proxy deployment

- configure `base_path` when serving the app under a prefix such as `/p/study-a`
- `/controller/*` endpoints should be protected with `BULLETJOURNAL_CONTROLLER_TOKEN`
- editor sessions are proxied through the main app origin, not exposed as raw localhost URLs

## Restart behavior

- managed runs execute in subprocesses
- one managed run is allowed per project at a time in the MVP
- queued/running records are marked `aborted_on_restart` when a project is reopened
- SSE history is in-memory only and is reset on process restart

## Failure investigation

- check `metadata/state.db` for persisted run, artifact, and validation state
- inspect `graph/*.json` if graph operations behave unexpectedly
- inspect notebook source hashes and validation issues through `/api/v1/project/snapshot`
- rerun with `PYTHONPATH=src python -m pytest` for reproducible failures in this repo; this uses four workers and randomized seed `42`
- add `-n 0` to rerun serially when diagnosing timing-sensitive failures

## Notes

- one active managed run is allowed per project in the MVP
- in-flight runs are marked aborted on restart
- file uploads use raw request bytes plus `X-Filename` header in the current shell UI
