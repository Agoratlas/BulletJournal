# Project Format

BulletJournal projects are portable directories with a canonical locked environment definition.

## Canonical layout

```text
project_root/
|- graph/
|- notebooks/
|- artifacts/
|  `- objects/
|- metadata/
|  |- project.json
|  `- state.db
|- checkpoints/
|- uploads/
|  `- temp/
|- pyproject.toml
`- uv.lock
```

## Stable metadata

`metadata/project.json` stores project-owned metadata, including:

- `schema_version`
- `project_id`
- `created_at`

`project_id` is stable across export/import and must match `^[a-z0-9][a-z0-9_-]{1,62}$`.

## Environment definition

- `pyproject.toml` and `uv.lock` at the project root are authoritative
- `metadata/environment.json` is no longer part of the canonical format
- `metadata/environment_packages.txt` is no longer part of the canonical format

## SQLite state

`metadata/state.db` stores mutable execution metadata such as notebook revisions, notices, artifacts, runs, checkpoints, and controller-facing activity timestamps.
