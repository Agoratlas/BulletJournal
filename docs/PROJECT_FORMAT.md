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

## Graph node kinds

The graph stored under `graph/` includes node records whose `kind` is currently one of:

- `notebook`
- `file_input`
- `organizer`
- `area`

`organizer` nodes persist their synthetic passthrough ports in `node.ui.organizer_ports`.

Each organizer port is stored as:

```json
{ "key": "train", "name": "Train", "data_type": "dataframe" }
```

`area` nodes are visual-only and persist style data in `node.ui`:

```json
{
  "title_position": "top-left",
  "area_color": "blue",
  "area_filled": true
}
```

`area` nodes have layout entries like any other node, and their `w` / `h` values determine the visible rectangle size.
