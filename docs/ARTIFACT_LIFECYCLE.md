# Artifact Lifecycle

## State database compatibility

Opening or importing a project automatically applies state database migrations. These migrations are one-way: after
the state database is upgraded, older BulletJournal executables may no longer be able to open it. Project and graph
schema versions are independent of this internal state database migration history.

## Incarnations and publication

Graph blocks have immutable server-generated incarnation IDs. Renaming a block preserves its incarnation, while a new
block that reuses a deleted logical ID receives a new incarnation and cannot inherit the deleted block's revisions,
heads, execution metadata, or notices. Output publication is generation-fenced and staged so failed, cancelled, or
superseded runs do not replace public heads with partial results.

Deleting a block creates a retained tombstone instead of immediately destroying its state. Browser undo restores the
same incarnation and exact saved artifact, asset, and execution heads. Undo reports a conflict while another live block
uses the deleted logical ID, and reports expiry after the configured retention period. Restored heads are marked ready
only after their object integrity and current source/upstream lineage are proven; otherwise they become stale or
pending.

## Checkpoints and startup

Checkpoints include a checksummed `heads.json` manifest with exact incarnation, head, version, lineage, and object
metadata. Object payloads are not copied into checkpoints and checkpoint references do not pin storage. Restoring an
old checkpoint therefore recovers each independently available head and leaves missing or corrupt outputs pending with
a notice.

On project startup, BulletJournal verifies attached object size and SHA-256 content before exposing ready state.
Unavailable objects become pending, unprovable lineage becomes stale, and a damaged block does not prevent other
blocks from loading. Corrupt files are moved aside for diagnostics; this integrity quarantine is separate from garbage
collection.

## Garbage collection

Garbage collection protects artifact and asset versions, retained tombstones, open publications, object pins, and
active leases. Old unprotected versions are pruned before unreferenced objects are considered for deletion. Checkpoints
and completed run provenance are intentionally weak references. Execution logs and checkpoint directories are excluded
from temporary-storage cleanup.

Each object records when its final strong reference disappears. `gc_object_retention_seconds` is measured from that
time, not from object creation. A new reference clears the timestamp and restarts retention if the object later becomes
unreferenced again. Once the retention period has elapsed, a GC pass rechecks references and directly deletes the
object file and metadata row. There is no recoverable GC quarantine stage. Deletion can happen later than the configured
period because collection is bounded and only occurs when a GC pass runs.

Per-project policy is stored in `metadata/state.db`'s `project_meta` table. Supported keys include:

- `gc_enabled`, default `true`
- `gc_object_retention_seconds`, default `3600`
- `gc_version_retention_seconds`, default `3600`
- `gc_min_interval_seconds`, default `900`
- `gc_temp_retention_seconds`, default `86400`
- `gc_batch_size`, default `250`
- `gc_max_batch_bytes`, default 64 MiB

Invalid values are rejected. Edits request throttled background maintenance and do not synchronously delete files.

Maintenance endpoints:

- `GET /api/v1/maintenance/gc/status` returns current GC status and policy.
- `POST /api/v1/maintenance/gc` runs the bounded collector and defaults to dry-run reporting.
