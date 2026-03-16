# Project Format

BulletJournal projects are portable directories.

## Directories

```text
project_root/
├─ graph/
├─ notebooks/
├─ artifacts/objects/
├─ metadata/
├─ checkpoints/
└─ uploads/temp/
```

## Graph files

- `graph/meta.json`: schema version, project id, graph version, updated timestamp
- `graph/nodes.json`: notebook and file input node definitions
- `graph/edges.json`: visible artifact connections
- `graph/layout.json`: node positions and sizes
- graph writes are directory-atomic: all four files are replaced as one graph snapshot

## SQLite tables

`metadata/state.db` contains:

- notebook revisions and validation issues
- artifact objects, versions, heads, and cache index
- run records, run inputs, run outputs
- checkpoints

Important table roles:

- `notebook_revisions`: parsed interfaces keyed by `(node_id, source_hash)`
- `validation_issues`: durable parser and graph-facing warnings
- `artifact_objects`: content-addressed metadata for stored blobs
- `artifact_versions`: immutable lineage records per produced artifact version
- `artifact_heads`: current visible version and state (`pending`, `ready`, `stale`)
- `cache_index`: upstream-data-hash lookup and nondeterminism tracking
- `run_records`, `run_inputs`, `run_outputs`: run lifecycle plus loaded/produced lineage

## Checkpoints

Checkpoints copy `graph/` and `notebooks/` only.
Artifact objects remain in the shared cache and are reconciled on restore.

On restore the backend:

- restores graph and notebook files
- reparses notebooks
- removes state for nodes/artifacts no longer present
- recreates missing artifact heads for restored interfaces
- marks restored notebook outputs stale so users can rerun against the restored graph
