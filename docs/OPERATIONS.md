# Operations

## Start locally

```bash
bulletjournal start .
```

`bulletjournal start` fails fast unless the path is already a valid BulletJournal project root.

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
- rerun with `PYTHONPATH=src python -m pytest` for reproducible failures in this repo

## Notes

- one active managed run is allowed per project in the MVP
- in-flight runs are marked aborted on restart
- file uploads use raw request bytes plus `X-Filename` header in the current shell UI
