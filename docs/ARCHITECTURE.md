# Architecture

BulletJournal is a single-project application and is split into these layers:

- `bulletjournal.domain`: enums, models, hashing, graph rules
- `bulletjournal.storage`: project filesystem, object store, SQLite state
- `bulletjournal.parser`: AST notebook interface extraction
- `bulletjournal.runtime`: artifact SDK used inside Marimo notebooks
- `bulletjournal.execution`: worker manifests, notebook execution, Marimo sessions
- `bulletjournal.services`: project, graph, artifact, run, checkpoint orchestration
- `bulletjournal.api`: REST, SSE, and bundled frontend serving
- `bulletjournal.cli`: local commands for init/start/dev/doctor/rebuild/import/export

Key architectural rules:

- project root is the persistence boundary
- one server process is bound to one project root at startup
- SQLite is authoritative for mutable execution metadata
- `pyproject.toml` and `uv.lock` are authoritative for project environments
- artifact payloads are content-addressed on disk
- runs happen in subprocesses, never inside the API server process
- notebook interfaces are parsed from source, not declared separately
- runtime artifact pushes are validated against the parsed interface before persistence
- graph persistence is snapshot-oriented; callers should treat `graph/` as one atomic unit
- restore/delete flows must reconcile SQLite state with the current graph and notebook set
- SSE is an in-memory notification layer, not the source of truth; clients must recover from `stream.reset` by refetching snapshot state
- editor sessions are proxied back through the main app origin
- templates can be discovered from installed provider packages
