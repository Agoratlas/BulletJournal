# Operations

## Start locally

```bash
bulletjournal start .
```

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

## Restart behavior

- managed runs execute in subprocesses
- one managed run is allowed per project at a time in the MVP
- queued/running records are marked `aborted_on_restart` when a project is reopened
- SSE history is in-memory only and is reset on process restart

## Failure investigation

- check `metadata/state.db` for persisted run, artifact, and validation state
- inspect `graph/*.json` if graph operations behave unexpectedly
- inspect notebook source hashes and validation issues through `/api/v1/projects/{project_id}/snapshot`
- rerun with `PYTHONPATH=src python -m pytest` for reproducible failures in this repo

## Notes

- one active managed run is allowed per project in the MVP
- in-flight runs are marked aborted on restart
- file uploads use raw request bytes plus `X-Filename` header in the current shell UI
