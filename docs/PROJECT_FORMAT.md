# Project Format

BulletJournal projects are portable directories with a canonical project layout and schema metadata.

## Canonical layout

```text
project_root/
|- graph/
|- notebooks/
|- objects/
|- dashboards/
|- metadata/
|  |- project.json
|  `- state.db
|- checkpoints/
`- temp/
   |- uploads/
   |- execution_logs/
   `- worker/
|- pyproject.toml
`- uv.lock
```

## Ownership

BulletJournal-owned files and directories:

- `graph/meta.json`
- `graph/nodes.json`
- `graph/edges.json`
- `graph/layout.json`
- `metadata/project.json`
- `metadata/state.db`
- `notebooks/`
- `objects/`
- `dashboards/`
- `checkpoints/`
- `temp/uploads/`
- `temp/execution_logs/`
- `temp/worker/`

Environment files at the project root may be managed either by BulletJournal or by an external controller:

- `pyproject.toml`
- `uv.lock`

For controller-managed environments, run:

```bash
bulletjournal init /project --project-id study-a --skip-environment
```

That command creates or repairs the BulletJournal-owned layout without creating or replacing `pyproject.toml` or `uv.lock`.

## Stable metadata

`metadata/project.json` stores project-owned metadata, including:

- `schema_version`
- `project_id`
- `created_at`

`project_id` is stable across export/import and must match `^[a-z0-9][a-z0-9_-]{1,62}$`.

`schema_version` in `metadata/project.json` must equal `2`. Projects with any other project schema version are rejected.

## Environment definition

- `pyproject.toml` and `uv.lock` at the project root define the environment when BulletJournal manages it
- external controllers may manage those files instead and use `bulletjournal init --skip-environment` for layout-only bootstrap
- `metadata/environment.json` is no longer part of the canonical format
- `metadata/environment_packages.txt` is no longer part of the canonical format

## SQLite state

`metadata/state.db` stores mutable execution metadata such as notebook revisions, notices, artifacts, runs, checkpoints, and controller-facing activity timestamps.

## Initialization semantics

Initialization is idempotent by design:

- existing valid layout files are left in place
- missing required files and directories are created
- missing transient runtime directories such as `dashboards/` or `temp/execution_logs/` are recreated
- unsupported schema versions fail fast
- `--skip-environment` never creates, overwrites, or repairs `pyproject.toml` and `uv.lock`

## Graph node kinds

The graph stored under `graph/` includes node records whose `kind` is currently one of:

- `notebook`
- `constant`
- `organizer`
- `area`

`constant` nodes persist their synthetic output metadata in `node.ui`:

```json
{ "artifact_name": "value", "data_type": "int" }
```

If a pipeline template instantiates a constant with a pre-populated `value`, that value is stored as the constant block's managed artifact state, not in `node.ui`.

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
