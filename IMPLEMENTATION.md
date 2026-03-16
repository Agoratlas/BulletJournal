**BulletJournal Execution Plan**

Version: MVP architecture plan v1  
Status: design locked enough to begin implementation  
Audience: engineers building BulletJournal from scratch  
Primary goal: two different engineers should converge on materially the same implementation

**1. Purpose**

BulletJournal is a notebook orchestration platform for reproducible data science built on top of Marimo, with a graph editor, explicit artifact passing, persistent on-disk state, and strong stale-state detection across notebooks.

This document defines the implementation plan, architecture, storage format, execution semantics, developer experience, and phased roadmap for the MVP and the near-term growth path.

**2. Product Vision**

- Replace hidden notebook state with explicit, persisted, typed artifacts.
- Make pipelines first-class, not an afterthought.
- Preserve the flexibility of arbitrary Python code.
- Keep the common local workflow as simple as Jupyter: one command to start everything.
- Favor correctness and recoverability over cleverness.
- Keep the MVP narrow enough to ship, but modular enough to support later cell-level invalidation and stronger isolation.

**3. Locked Decisions**

- Marimo remains the notebook substrate.
- The backend is Python with FastAPI.
- The editor frontend is React + TypeScript + ReactFlow.
- The backend is the only source of truth for graph integrity, artifact states, and execution planning.
- The project root on disk is the canonical state boundary.
- In-memory state may be ahead by a few seconds, but it must always converge to disk promptly.
- The graph format is deterministic JSON split across multiple files under `graph/`.
- Mutable execution metadata lives in one per-project SQLite database: `metadata/state.db`.
- Artifact payload bytes live in a content-addressed object store under `artifacts/objects/`.
- Notebook IDs are immutable human-readable slugs and also filename stems.
- Notebook titles are editable display labels and do not affect identity.
- MVP invalidation is notebook-level across notebooks.
- The architecture must keep the dependency analyzer modular so cell-level invalidation can be added later without a storage rewrite.
- AST parsing is the source of truth for notebook inputs/outputs/assets in the editor.
- No explicit interface declaration cells are added in MVP.
- Notebook interface parsing is intentionally strict and rejects dynamic/indirect artifact declarations.
- Port rename or port type change is treated as delete + recreate; connected edges are deleted and a warning is shown.
- Managed runs and interactive `Edit & Run` are distinct provenance modes.
- Interactive runs use a 2-second save stabilization heuristic before artifact registration and are marked as heuristic lineage.
- Partial output persistence on notebook failure is allowed; outputs already pushed before the failure remain valid.
- One active run per project in MVP.
- Backend-to-frontend live updates use HTTP + SSE, not WebSockets.
- Local-first startup must be one command after installation.
- `uv` is the default Python environment/tool runner for development and the recommended local workflow, while normal wheel install remains supported.

**4. MVP Scope**

- Notebook nodes
- File input nodes
- Value input nodes implemented as built-in notebook templates
- Deterministic graph persistence
- AST-derived notebook ports and docs
- Managed single-notebook and multi-notebook execution
- Artifact storage for JSON-like values, DataFrames, Series, files, and explicit `object`
- Ready / stale / pending artifact states
- Artifact explorer APIs and basic UI
- Notebook templates
- Disaster-recovery checkpoints
- Single-command local startup
- SSE-based live UI updates

**5. Explicit Non-Goals For MVP**

- Cell-level stale propagation across notebooks
- Pipeline templates
- Multi-user RBAC
- Full container orchestration
- AI-agent execution support
- Arbitrary direct artifact pulls by full path in the first shipping slice
- Resumable distributed work queues
- Advanced cache eviction policies beyond a basic persisted LRU model
- Production-grade multi-tenant security isolation

**6. Architecture Overview**

BulletJournal has five runtime pieces:

- A Python CLI that starts BulletJournal in local or development mode.
- A FastAPI backend that owns graph state, notebook parsing, artifact storage, run orchestration, and APIs.
- A worker subprocess model for notebook execution so analyst code does not run inside the API server process.
- A React frontend served by the backend in normal mode and by Vite only in contributor mode.
- Marimo editor subprocesses launched on demand and proxied through the backend.

The common local setup is one process tree started by one command. The backend serves the built frontend, launches notebook workers, and launches Marimo edit sessions when requested.

**7. High-Level Process Model**

```text
User CLI
  -> starts BulletJournal backend
  -> backend opens project root
  -> backend serves ReactFlow UI
  -> UI calls HTTP API and listens to SSE
  -> backend parses notebooks and validates graph
  -> backend launches worker subprocesses for managed runs
  -> workers use bulletjournal.runtime.artifacts inside notebooks
  -> worker persists artifacts and lineage into object store + SQLite
  -> backend emits SSE updates to UI
```

**8. Repository Layout**

Target repo layout:

```text
.
├─ pyproject.toml
├─ ruff.toml
├─ .pre-commit-config.yaml
├─ src/
│  └─ bulletjournal/
│     ├─ __init__.py
│     ├─ config.py
│     ├─ cli/
│     │  ├─ __init__.py
│     │  ├─ app.py
│     │  ├─ init_project.py
│     │  ├─ start.py
│     │  ├─ dev.py
│     │  ├─ doctor.py
│     │  ├─ validate_templates.py
│     │  └─ rebuild_state.py
│     ├─ domain/
│     │  ├─ __init__.py
│     │  ├─ models.py
│     │  ├─ enums.py
│     │  ├─ graph_rules.py
│     │  ├─ type_system.py
│     │  ├─ hashing.py
│     │  ├─ state_machine.py
│     │  └─ errors.py
│     ├─ parser/
│     │  ├─ __init__.py
│     │  ├─ marimo_loader.py
│     │  ├─ interface_parser.py
│     │  ├─ docs_parser.py
│     │  ├─ source_hash.py
│     │  └─ validation.py
│     ├─ storage/
│     │  ├─ __init__.py
│     │  ├─ project_fs.py
│     │  ├─ graph_store.py
│     │  ├─ object_store.py
│     │  ├─ state_db.py
│     │  ├─ migrations.py
│     │  └─ atomic_write.py
│     ├─ runtime/
│     │  ├─ __init__.py
│     │  ├─ artifacts.py
│     │  ├─ context.py
│     │  ├─ serializers.py
│     │  ├─ file_artifacts.py
│     │  └─ warnings.py
│     ├─ execution/
│     │  ├─ __init__.py
│     │  ├─ manifests.py
│     │  ├─ planner.py
│     │  ├─ runner.py
│     │  ├─ worker_main.py
│     │  ├─ marimo_adapter.py
│     │  ├─ sessions.py
│     │  └─ watcher.py
│     ├─ services/
│     │  ├─ __init__.py
│     │  ├─ project_service.py
│     │  ├─ graph_service.py
│     │  ├─ notebook_service.py
│     │  ├─ artifact_service.py
│     │  ├─ run_service.py
│     │  ├─ checkpoint_service.py
│     │  ├─ template_service.py
│     │  └─ event_service.py
│     ├─ api/
│     │  ├─ __init__.py
│     │  ├─ app.py
│     │  ├─ sse.py
│     │  ├─ deps.py
│     │  ├─ errors.py
│     │  ├─ schemas.py
│     │  └─ routes/
│     │     ├─ project.py
│     │     ├─ graph.py
│     │     ├─ notebooks.py
│     │     ├─ artifacts.py
│     │     ├─ runs.py
│     │     ├─ checkpoints.py
│     │     └─ templates.py
│     ├─ templates/
│     │  ├─ __init__.py
│     │  ├─ registry.py
│     │  ├─ validator.py
│     │  └─ builtin/
│     │     ├─ empty_notebook.py
│     │     └─ value_input.py
│     └─ _web/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ api/
│  ├─ e2e/
│  └─ fixtures/
├─ web/
│  ├─ package.json
│  ├─ tsconfig.json
│  ├─ vite.config.ts
│  └─ src/
└─ docs/
```

**9. Project Root Layout**

Target project layout on disk:

```text
project_root/
├─ graph/
│  ├─ meta.json
│  ├─ nodes.json
│  ├─ edges.json
│  └─ layout.json
├─ notebooks/
│  ├─ parse_user_csv.py
│  ├─ extract_communities.py
│  └─ final_report.py
├─ artifacts/
│  ├─ objects/
│  │  ├─ a1/
│  │  │  └─ d8098ceddc122be684385ff870f8d361a072ff5cbdf72661f2c27e9c1bb994
├─ metadata/
│  ├─ project.json
│  ├─ environment.json
│  └─ state.db
├─ checkpoints/
│  ├─ 2026-03-11T17-13-46Z/
│  │  ├─ graph/
│  │  └─ notebooks/
└─ uploads/
   └─ temp/
```

Rules:

- `graph/` is human-edited only through BulletJournal.
- `notebooks/` is human-edited through Marimo and normal editors.
- `artifacts/objects/` is immutable once a blob exists.
- `metadata/state.db` is machine-owned.
- `checkpoints/` contains only graph and notebook snapshots, not artifact blob copies.
- `uploads/temp/` is ephemeral and may be cleaned.

**10. Graph File Format**

Use deterministic JSON with stable ordering.

`graph/meta.json` contains project-level graph metadata only.

Example:

```json
{
  "schema_version": 1,
  "project_id": "study_2026_03",
  "graph_version": 42,
  "updated_at": "2026-03-12T10:15:00Z"
}
```

`graph/nodes.json` contains persistent node definitions.

Example:

```json
[
  {
    "id": "parse_user_csv",
    "kind": "notebook",
    "title": "Parse user CSV",
    "path": "notebooks/parse_user_csv.py",
    "template": null,
    "ui": {
      "hidden_inputs": []
    }
  },
  {
    "id": "input_dataset",
    "kind": "file_input",
    "title": "User CSV"
  }
]
```

`graph/edges.json` contains only user-defined visible edges.

Example:

```json
[
  {
    "id": "input_dataset.file__parse_user_csv.file",
    "source_node": "input_dataset",
    "source_port": "file",
    "target_node": "parse_user_csv",
    "target_port": "file"
  }
]
```

`graph/layout.json` contains only coordinates and dimensions.

Example:

```json
[
  {
    "node_id": "input_dataset",
    "x": 80,
    "y": 120,
    "w": 320,
    "h": 220
  },
  {
    "node_id": "parse_user_csv",
    "x": 480,
    "y": 120,
    "w": 340,
    "h": 260
  }
]
```

Rules:

- Arrays are sorted by stable IDs.
- JSON keys are written in a fixed order.
- Volatile runtime state is never stored in graph files.
- Derived notebook interfaces are not stored in graph files; they are reparsed on startup and cached in SQLite.

**11. Domain Model**

Core entities:

- `Project`
- `Node`
- `Edge`
- `NotebookInterface`
- `Port`
- `ArtifactObject`
- `ArtifactVersion`
- `ArtifactHead`
- `Run`
- `Checkpoint`
- `ValidationIssue`
- `TemplateRef`
- `MarimoSession`

Node kinds:

- `notebook`
- `file_input`

Value input behavior:

- Implemented as a notebook node created from the built-in `value_input.py` template.
- No separate runtime special case is needed.

Artifact roles:

- `output`
- `asset`

Artifact states:

- `ready`
- `stale`
- `pending`

Run modes:

- `run_stale`
- `run_all`
- `edit_run`

Lineage modes:

- `managed`
- `interactive_heuristic`

Validation severities:

- `error`
- `warning`

**12. Notebook Identity**

- Each notebook node has one immutable slug like `parse_user_csv`.
- The file path is `notebooks/parse_user_csv.py`.
- The node ID equals the filename stem.
- The display title is mutable and stored in `graph/nodes.json`.
- Node IDs cannot be renamed in MVP.
- Creating a new notebook requires title + unique slug.
- Template-derived notebooks record origin metadata in `graph/nodes.json`.

**13. Notebook Authoring Contract**

Accepted import form:

```python
from bulletjournal.runtime import artifacts
```

Accepted patterns:

```python
graph_df = artifacts.pull(name='graph_df', data_type=pd.DataFrame)
count = artifacts.pull(name='count', data_type=int, default=10)

artifacts.push(df, name='processed_df', is_output=True, data_type=pd.DataFrame)
artifacts.push(summary, name='summary_md', data_type=str)

with artifacts.push_file(name='plot', extension='.png') as out_path:
    plt.savefig(out_path)
```

Strict parser rules:

- The imported runtime name must be exactly `artifacts`.
- Only direct calls to `artifacts.pull`, `artifacts.pull_file`, `artifacts.push`, and `artifacts.push_file` are parsed.
- Calls must appear at the top level of the cell body.
- No aliasing.
- No wrappers.
- No helper variables pointing to artifact functions.
- No conditionals or loops around artifact declarations.
- `name=` must be a literal string.
- `is_output=` must be a literal boolean when present.
- `description=` must be a literal string when present.
- `default=` must be a literal JSON-serializable value.
- `data_type=` must be statically parseable to a canonical type or explicitly be `object`.
- `push_file` must be a top-level `with` statement.
- The first Markdown cell, if present, becomes the notebook documentation shown in the graph UI.
- The parser must reject unsupported syntax with a clear validation issue.

Rejected patterns:

```python
p = artifacts.push
p(df, name='x', data_type=pd.DataFrame)
```

```python
if use_export:
    artifacts.push(df, name='x', data_type=pd.DataFrame)
```

```python
name = 'x'
artifacts.push(df, name=name, data_type=pd.DataFrame)
```

Parser behavior:

- Structural violations create `error` validation issues.
- Unparseable type expressions create `warning` issues and normalize to `object`.
- Duplicate input/output names within a notebook create `error` issues.
- A notebook with parser `error` issues is visible in the UI but blocked from managed execution.

**14. Supported Type System**

Canonical type tags in MVP:

- `int`
- `float`
- `bool`
- `str`
- `list`
- `dict`
- `pandas.DataFrame`
- `pandas.Series`
- `networkx.Graph`
- `networkx.DiGraph`
- `file`
- `object`

Compatibility rule:

- Exact type match only in MVP.
- `object` only matches `object`.
- `file` only matches `file`.

Reasoning:

- Exact matching keeps the UI honest.
- `object` remains an explicit escape hatch rather than a silent universal adapter.

**15. Artifact Storage Model**

Artifact layers:

- `ArtifactObject`: immutable stored bytes keyed by `artifact_hash`
- `ArtifactVersion`: a produced version with lineage, hashes, and role
- `ArtifactHead`: the currently visible version and state for `(node_id, artifact_name)`

Serialization rules:

- JSON-compatible simple values -> UTF-8 JSON bytes
- `pandas.DataFrame` -> Parquet
- `pandas.Series` -> Parquet or single-column Parquet
- `file` -> raw bytes with extension and MIME metadata
- `object` -> compressed pickle

Object store path:

```text
artifacts/objects/<first-two-chars>/<remaining-hash>
```

Example:

```text
artifacts/objects/a1/d8098ceddc122be684385ff870f8d361a072ff5cbdf72661f2c27e9c1bb994
```

Rules:

- Object paths contain only bytes, not metadata.
- All metadata lives in SQLite.
- File artifact writes always go through a temp file.
- Temp file promotion into the object store is atomic.
- Stored object blobs are read-only after creation.

**16. SQLite Database**

Use one SQLite database per project: `metadata/state.db`.

Do not use an ORM in MVP.

Use:

- stdlib `sqlite3`
- explicit SQL migration files
- explicit row mapping in Python
- WAL mode for better concurrent read behavior
- foreign keys enabled
- atomic transactions around all state mutations

Required tables:

- `schema_migrations`
- `project_meta`
- `notebook_revisions`
- `validation_issues`
- `artifact_objects`
- `artifact_versions`
- `artifact_heads`
- `run_records`
- `run_inputs`
- `run_outputs`
- `cache_index`
- `checkpoints`
- `event_log` optional in MVP; omit unless needed

Recommended columns:

`artifact_objects`
- `artifact_hash TEXT PRIMARY KEY`
- `storage_kind TEXT NOT NULL`
- `data_type TEXT NOT NULL`
- `size_bytes INTEGER NOT NULL`
- `extension TEXT NULL`
- `mime_type TEXT NULL`
- `preview_json TEXT NULL`
- `created_at TEXT NOT NULL`
- `last_accessed_at TEXT NOT NULL`
- `nondeterministic INTEGER NOT NULL DEFAULT 0`

`artifact_versions`
- `version_id INTEGER PRIMARY KEY`
- `node_id TEXT NOT NULL`
- `artifact_name TEXT NOT NULL`
- `role TEXT NOT NULL`
- `artifact_hash TEXT NOT NULL`
- `source_hash TEXT NOT NULL`
- `upstream_code_hash TEXT NOT NULL`
- `upstream_data_hash TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `lineage_mode TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `warning_json TEXT NOT NULL DEFAULT '[]'`

`artifact_heads`
- `node_id TEXT NOT NULL`
- `artifact_name TEXT NOT NULL`
- `current_version_id INTEGER NULL`
- `state TEXT NOT NULL`
- `PRIMARY KEY (node_id, artifact_name)`

`run_records`
- `run_id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `mode TEXT NOT NULL`
- `status TEXT NOT NULL`
- `target_json TEXT NOT NULL`
- `graph_version INTEGER NOT NULL`
- `source_snapshot_json TEXT NOT NULL`
- `started_at TEXT NULL`
- `ended_at TEXT NULL`
- `failure_json TEXT NULL`

`run_inputs`
- `run_id TEXT NOT NULL`
- `logical_artifact_id TEXT NOT NULL`
- `artifact_hash_at_load TEXT NOT NULL`
- `state_at_load TEXT NOT NULL`
- `loaded_at TEXT NOT NULL`

`run_outputs`
- `run_id TEXT NOT NULL`
- `node_id TEXT NOT NULL`
- `artifact_name TEXT NOT NULL`
- `version_id INTEGER NOT NULL`

`cache_index`
- `node_id TEXT NOT NULL`
- `artifact_name TEXT NOT NULL`
- `upstream_data_hash TEXT NOT NULL`
- `artifact_hash TEXT NOT NULL`
- `is_nondeterministic INTEGER NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`
- `PRIMARY KEY (node_id, artifact_name, upstream_data_hash)`

`notebook_revisions`
- `node_id TEXT NOT NULL`
- `source_hash TEXT NOT NULL`
- `saved_at TEXT NOT NULL`
- `doc_excerpt TEXT NULL`
- `interface_json TEXT NOT NULL`
- `PRIMARY KEY (node_id, source_hash)`

`validation_issues`
- `issue_id TEXT PRIMARY KEY`
- `node_id TEXT NOT NULL`
- `severity TEXT NOT NULL`
- `code TEXT NOT NULL`
- `message TEXT NOT NULL`
- `details_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`

`checkpoints`
- `checkpoint_id TEXT PRIMARY KEY`
- `created_at TEXT NOT NULL`
- `graph_version INTEGER NOT NULL`
- `path TEXT NOT NULL`
- `restored_at TEXT NULL`

**17. Project Metadata**

`metadata/project.json` should contain stable project configuration.

Example:

```json
{
  "schema_version": 1,
  "project_id": "study_2026_03",
  "title": "Study 2026 03",
  "created_at": "2026-03-12T09:00:00Z",
  "artifact_cache_limit_bytes": 20000000000,
  "tracked_env_vars": [],
  "default_open_browser": true
}
```

`metadata/environment.json` should contain reproducibility metadata.

Example:

```json
{
  "python_version": "3.11.9",
  "bulletjournal_version": "0.1.0",
  "marimo_version": "0.20.4",
  "package_snapshot_format": "pip_freeze_text",
  "package_snapshot_path": "metadata/environment_packages.txt",
  "tracked_env_vars": []
}
```

Rules:

- Track env vars only if explicitly configured.
- Do not persist arbitrary environment variables by default.
- The environment snapshot is metadata, not a correctness mechanism.
- Artifact freshness does not depend on environment snapshots in MVP.

**18. Hashing Model**

Required hashes:

- `source_hash`: hash of the current notebook source file bytes
- `artifact_hash`: hash of persisted artifact bytes
- `upstream_code_hash`: hash of logical computation path
- `upstream_data_hash`: hash of actual imported artifact hashes plus logical computation path

MVP freshness rule:

- Use notebook-level invalidation across notebooks.
- The hashing API must still be designed so cell-level hashing can replace notebook-level code hashing later.

Notebook-level implementation:

- `upstream_code_hash` for an artifact is derived from:
  - current notebook `source_hash`
  - source artifacts’ `upstream_code_hash` values
  - artifact logical identity `(node_id, artifact_name)`

- `upstream_data_hash` for an artifact is derived from:
  - current notebook `source_hash`
  - imported artifact `artifact_hash` values actually loaded during the run
  - artifact logical identity `(node_id, artifact_name)`

Rules:

- Freshness is never determined from timestamps alone.
- The runtime must record the exact artifact hashes seen at pull time.
- If the same `upstream_data_hash` later produces a different `artifact_hash`, update `cache_index` to the newest artifact, mark that cache key nondeterministic, and surface that status to restore flows.
- On checkpoint restore, if the restored state references nondeterministic artifacts, the user is prompted whether to keep them or mark them stale.

**19. Staleness Rules**

An artifact becomes `stale` when:

- The producing notebook `source_hash` changes.
- Any upstream connected artifact head changes.
- A visible edge is added, removed, or retargeted.
- A port is renamed or deleted.
- A port type changes.
- A file input receives a new upload.

An artifact becomes `pending` when:

- It has never been produced.
- Its port now exists but no version exists yet.
- A checkpoint restore references a version no longer available and no fallback exists.

An artifact becomes `ready` when:

- A valid version exists and no current invalidation rule marks it stale.

Rules:

- Stale artifacts keep their last value until replaced.
- Pending artifacts have no value.
- Pulling stale artifacts is allowed with warnings.
- Pulling pending artifacts raises immediately.

**20. Port Diff Behavior**

When a notebook is reparsed:

- New ports are added.
- Deleted ports remove corresponding `artifact_heads`.
- Renamed ports are treated as delete + add.
- Type changes are treated as delete + add.
- Any edge touching a deleted or type-changed port is removed automatically.
- The backend creates validation warnings describing removed edges.
- The frontend shows these warnings prominently in the main editor.

**21. Managed Execution Model**

MVP managed execution is notebook-level, not cell-level.

A managed run:

- Freezes the current graph version.
- Freezes notebook source hashes.
- Resolves input bindings.
- Spawns a worker subprocess.
- Executes the notebook.
- Records all pulls and pushes via the runtime API.
- Persists artifact versions atomically as they are completed.
- Updates artifact heads and states.
- Emits SSE events.

In MVP, a managed notebook run executes the full notebook rather than a cell-targeted subset.

Reasoning:

- This keeps the first runner reliable.
- It avoids prematurely depending on deeper Marimo internal scheduling.
- The dependency analyzer remains modular so targeted cell execution can be added later.

**22. Interactive Edit & Run Model**

`Edit & Run` behavior:

- The backend launches a Marimo editor session for the notebook.
- The UI opens the proxied editor URL in a new tab or panel.
- The notebook runtime still uses `bulletjournal.runtime.artifacts`.
- When an artifact is pushed during interactive execution, BulletJournal waits for notebook source stability for 2 seconds before finalizing artifact registration.
- The resulting `ArtifactVersion` is marked `lineage_mode='interactive_heuristic'`.
- Managed runs for that same notebook are blocked while the edit session is active.
- Interactive sessions do not change the graph structure directly; graph changes still come from reparsing saved files.

Rules:

- Interactive lineage is weaker than managed lineage.
- The UI must surface that distinction.
- This is acceptable in MVP and explicitly documented.

**23. Failure Semantics**

If a notebook run fails:

- Already-pushed artifacts remain persisted and may remain `ready` or `stale` depending on lineage.
- Untouched target outputs remain in their prior state.
- The run record is marked failed.
- The node enters error state in the UI.
- The user can inspect partial successful outputs.

This follows the rule that outputs already produced before the failing operation are valid because the failing operation is not in their execution graph.

**24. Queueing and Concurrency**

MVP concurrency rules:

- One active run per project.
- One in-memory project run queue.
- Queue order is a graph topological order.
- The queue is not persisted for restart recovery.
- On server restart, any `queued` or `running` run record is marked `aborted_on_restart`.

Queue abort conditions:

- Any notebook run fails.
- The graph is modified in a way that changes queued dependencies.
- A producing notebook source changes while queued work depends on it.
- The user explicitly stops the run.

When a node run is requested with stale or pending inputs:

- The backend computes the upstream impact.
- The API returns the information required to show the confirmation prompt.
- Allowed actions are:
  - `run_upstream`
  - `use_stale`
  - `cancel`

If `run_upstream` is chosen for `edit_run`:

- BulletJournal refreshes upstream work first.
- The editor is not opened automatically afterward.
- The user must click again when upstream refresh is complete.

**25. File Inputs**

File input node behavior:

- A file input node has one output port: `file`
- Type is always `file`
- Uploading a file creates a new artifact version
- Uploading a replacement marks downstream artifacts stale
- Metadata recorded:
  - original filename
  - size
  - extension
  - MIME type
  - upload timestamp

**26. Artifact Preview Rules**

Preview storage lives in SQLite as JSON, not as separate files.

Preview rules:

- Simple values: full JSON if small, truncated representation if large
- DataFrame/Series: shape + first rows + first columns
- Image files below a size limit: thumbnail preview metadata
- Other files: filename, size, extension, MIME type
- `object`: no rich preview; use serializer info and byte size

Preview generation must have hard limits to avoid memory spikes.

**27. Marimo Integration Boundary**

All Marimo-specific logic must be isolated behind two modules:

- `parser/marimo_loader.py`
- `execution/marimo_adapter.py`

Required capabilities:

- Load a Marimo notebook without executing user cell bodies for interface parsing.
- Extract the first Markdown cell for notebook docs.
- Execute a notebook headlessly in a worker subprocess.
- Launch an editor session for interactive mode.

This boundary is critical because Marimo internals may change. No Marimo internal imports should leak outside these modules.

**28. Frontend Architecture**

Use:

- React
- TypeScript
- Vite
- ReactFlow
- TanStack Query
- native `EventSource` for SSE

Do not add a heavy global state library in MVP unless needed later.

Primary pages:

- Graph editor
- Artifact explorer
- Checkpoint list
- Template browser
- Marimo editor session route or external tab target

Frontend responsibilities:

- Render nodes, edges, ports, and warnings
- Submit graph mutations over HTTP
- Start runs and show prompts
- Listen to SSE and refetch affected data
- Never compute freshness or graph integrity locally

Backend responsibilities:

- All validation
- All stale propagation
- All interface derivation
- All execution planning
- All artifact state transitions

**29. UI State Contract**

Node border color:

- green: all visible outputs ready
- yellow: one or more outputs stale
- grey: never run / pending only
- red: most recent run failed
- blue/pulsing: currently running

Port visuals:

- outer ring color = type
- inner fill = state

Bottom bar actions:

- `Run stale`
- `Run all`
- `Edit & Run`
- `View artifacts`

Warnings:

- Removed edges due to port rename/type change
- Pulling stale inputs
- Unknown type normalized to `object`
- Interactive heuristic lineage
- Nondeterministic cache key encountered

**30. HTTP API**

Use REST + SSE.

Base path:

```text
/api/v1
```

Important endpoints:

- `POST /projects/open`
- `POST /projects/init`
- `GET /projects/{project_id}/snapshot`
- `GET /projects/{project_id}/graph`
- `PATCH /projects/{project_id}/graph`
- `GET /projects/{project_id}/nodes/{node_id}`
- `POST /projects/{project_id}/nodes/{node_id}/run`
- `POST /projects/{project_id}/runs/{run_id}/cancel`
- `GET /projects/{project_id}/artifacts`
- `GET /projects/{project_id}/artifacts/{node_id}/{artifact_name}`
- `POST /projects/{project_id}/file-inputs/{node_id}/upload`
- `POST /projects/{project_id}/checkpoints`
- `GET /projects/{project_id}/checkpoints`
- `POST /projects/{project_id}/checkpoints/{checkpoint_id}/restore`
- `GET /projects/{project_id}/templates`
- `GET /projects/{project_id}/events`

Rules:

- Mutating graph calls require `graph_version`.
- Stale client writes return `409 Conflict`.
- Snapshot endpoints return all data the frontend needs to render current state.
- SSE is one-way only. On reconnect, the frontend refetches current snapshot.

**31. SSE Event Catalog**

Required event types:

- `project.opened`
- `graph.updated`
- `notebook.reparsed`
- `validation.updated`
- `artifact.state_changed`
- `run.queued`
- `run.started`
- `run.progress`
- `run.failed`
- `run.finished`
- `checkpoint.created`
- `checkpoint.restored`

Recommended event shape:

```json
{
  "event_type": "artifact.state_changed",
  "project_id": "study_2026_03",
  "graph_version": 42,
  "payload": {
    "node_id": "parse_user_csv",
    "artifact_name": "users_df",
    "old_state": "stale",
    "new_state": "ready"
  }
}
```

**32. CLI and Local Startup**

CLI entry point:

```toml
[project.scripts]
bulletjournal = "bulletjournal.cli.app:app"
```

Common user commands:

```bash
uvx bulletjournal init my-study
cd my-study
uvx bulletjournal
```

Installed tool commands:

```bash
bulletjournal init my-study
cd my-study
bulletjournal
```

Contributor commands:

```bash
uv sync --dev
uv run bulletjournal dev --open
```

Required CLI behavior:

- Running `bulletjournal` with no subcommand inside a project root behaves like `bulletjournal start .`
- `bulletjournal start [PATH]` starts backend + bundled frontend
- `bulletjournal dev [PATH]` starts backend reload mode and optionally Vite if available
- `bulletjournal doctor` checks environment and project health
- `bulletjournal validate-templates [PATH]` validates template directories
- `bulletjournal rebuild-state [PATH]` reparses notebooks and reconciles DB state from disk

Node is optional for normal local use.

Rules:

- Built frontend assets are bundled into the Python package
- Local analysts do not need Node installed
- Frontend contributors use `web/` with Vite and `pnpm`
- BulletJournal proxies or serves everything from one URL in normal local mode

**33. Python and Tooling**

Lock these choices:

- Python 3.11+
- `uv` for development and recommended local use
- setuptools build backend can remain initially
- `uv.lock` committed once introduced
- Ruff, ruff-format, pre-commit stay in place
- pytest for tests
- `pnpm` for the frontend if Node-based work is needed

Important repo change to make early:

- Raise `requires-python` in `pyproject.toml` to `>=3.11`

**34. Templates**

MVP template scope:

- Notebook templates only

Template sources:

- Read-only files outside project roots
- Loaded at backend startup
- Not hot-reloaded in MVP

Template metadata in nodes:

```json
{
  "template": {
    "kind": "template",
    "ref": "network/extract_communities.py",
    "origin_revision": "v1"
  }
}
```

Value input creation:

- Uses built-in template `value_input.py`
- The user edits it like any other notebook if needed

Pipeline templates:

- Explicitly post-MVP
- Keep the architecture open for them, but do not implement now

**35. Checkpoints**

MVP checkpoint purpose:

- Disaster recovery first
- UI restore later if time allows

Checkpoint contents:

- copy of `graph/`
- copy of `notebooks/`

Checkpoint rules:

- No artifact blobs copied
- No SQLite DB copied
- Restoring a checkpoint:
  - replaces `graph/`
  - replaces `notebooks/`
  - increments graph version
  - reparses notebooks
  - recomputes current heads/states
  - reuses cached artifact objects where possible
  - marks missing versions stale or pending

**36. Cache Eviction**

Persist eviction metadata in SQLite.

Do not use in-memory-only eviction as the source of truth.

MVP policy:

- configurable total cache size per project
- least-recently-accessed object candidates are evicted first
- objects referenced by current artifact heads are pinned
- evict only unpinned objects
- update `last_accessed_at` on artifact reads and downloads

Cold start behavior:

- no orphan cleanup heuristics are required if DB is authoritative
- optional reconciliation may remove orphan object files if found

**37. Security Model**

MVP trust model:

- single-user or trusted local environment
- arbitrary analyst Python code is allowed
- BulletJournal does not attempt to fully sandbox hostile code in MVP

Minimum safety measures:

- user code never runs in the API server process
- notebook workers run in subprocesses
- object store writes are mediated by the runtime API
- file artifact promotion is atomic
- project-root path boundaries are validated

Post-MVP:

- per-project containers
- stronger filesystem isolation
- multi-user access control

**38. Testing Strategy**

`tests/unit/`
- graph validation
- state transitions
- hashing determinism
- type parsing
- parser rule enforcement
- cache key behavior
- port diff behavior

`tests/integration/`
- project init/open
- graph persistence
- notebook reparsing on save
- artifact object store writes
- SQLite lineage writes
- run recovery after restart
- checkpoint restore
- file upload to file input node

`tests/api/`
- snapshot endpoint
- graph mutation conflicts
- node run endpoint
- cancel run endpoint
- artifact list/detail endpoints
- SSE smoke behavior

`tests/e2e/`
- create project
- add nodes and edges
- upload file
- run pipeline
- see artifact states update
- open Marimo session
- checkpoint and restore

Required regression scenarios:

- stale input used while upstream changed during editing
- same `upstream_data_hash` yields different artifact hash
- parser rejects aliased artifact calls
- type change removes edges
- interactive heuristic lineage is shown correctly
- server restart marks in-flight run as aborted and reloads project cleanly

**39. Documentation Deliverables**

Required docs to create during implementation:

- `README.md`
- `docs/EXECUTION_PLAN.md`
- `docs/ARCHITECTURE.md`
- `docs/PROJECT_FORMAT.md`
- `docs/NOTEBOOK_AUTHORING.md`
- `docs/API.md`
- `docs/TEMPLATES.md`
- `docs/OPERATIONS.md`
- `docs/TROUBLESHOOTING.md`

README must contain:

- what BulletJournal is
- install paths
- one-command quickstart
- project creation
- local startup
- common commands
- link to deeper docs

`docs/NOTEBOOK_AUTHORING.md` must contain:

- required import form
- accepted artifact syntax
- rejected syntax
- types
- outputs vs assets
- stale behavior
- interactive mode caveats

`docs/PROJECT_FORMAT.md` must contain:

- full project directory tree
- JSON file schemas
- SQLite table overview
- checkpoint semantics
- artifact object store format

**40. Implementation Order**

Phase 0: foundation spike
- Lock Python 3.11
- Add `uv` workflow
- Add CLI skeleton
- Confirm Marimo parsing and headless execution feasibility
- Confirm editor session launch strategy
- Create docs skeleton
- Exit criterion: one tiny notebook can be parsed and launched in Marimo through BulletJournal infrastructure code

Phase 1: storage and models
- Implement domain enums and models
- Implement deterministic graph JSON writer/reader
- Implement SQLite schema and migrations
- Implement object store
- Implement project init/open
- Exit criterion: BulletJournal can create/open a project and round-trip graph + DB state

Phase 2: parser and notebook sync
- Implement strict AST interface parser
- Implement docs extraction
- Implement source hash storage
- Implement validation issues
- Implement file watcher for `notebooks/`
- Implement port diff behavior and edge deletion
- Exit criterion: editing a notebook file updates ports, warnings, and stale states after save

Phase 3: runtime artifact SDK
- Implement `artifacts.pull`
- Implement `artifacts.pull_file`
- Implement `artifacts.push`
- Implement `artifacts.push_file`
- Implement serializers
- Implement artifact version/head writes
- Exit criterion: a controlled notebook execution can read and write artifacts with full lineage recorded

Phase 4: managed execution
- Implement run manifests
- Implement worker subprocess launcher
- Implement managed notebook execution
- Implement run cancellation
- Implement notebook-level invalidation and stale propagation
- Exit criterion: one notebook and then a two-notebook chain can be run end-to-end

Phase 5: backend APIs and SSE
- Implement snapshot API
- Implement graph mutation APIs
- Implement artifact explorer APIs
- Implement node run APIs
- Implement SSE stream
- Exit criterion: frontend can be built entirely against stable HTTP/SSE contracts

Phase 6: frontend MVP
- Implement graph editor
- Implement node cards and states
- Implement run prompts
- Implement artifact explorer
- Implement warnings UI
- Exit criterion: user can create/edit/run a project entirely from the UI

Phase 7: interactive edit mode
- Implement Marimo session launcher and proxy
- Add `Edit & Run`
- Add 2-second source stabilization heuristic
- Surface heuristic lineage in UI
- Exit criterion: notebook can be edited through BulletJournal and pushed artifacts appear correctly

Phase 8: templates and checkpoints
- Implement notebook template registry
- Implement template validation CLI
- Implement checkpoint create/list/restore
- Exit criterion: project recovery and notebook templating are usable in normal workflows

Phase 9: hardening
- Cache eviction
- crash reconciliation
- large file handling
- nondeterminism UI
- documentation polish
- release packaging
- Exit criterion: local single-user release candidate is stable

**41. Parallelization Plan For Two Engineers**

Engineer A primary scope:
- CLI
- project init/open
- graph JSON storage
- SQLite layer
- API layer
- SSE
- checkpoints
- docs and DX
- frontend integration once APIs stabilize

Engineer B primary scope:
- parser
- Marimo integration
- runtime artifact SDK
- worker execution
- hashing
- stale propagation
- file watcher
- notebook tests and execution tests

Shared contracts to lock before parallel work:

- graph JSON schemas
- SQLite table names and column names
- `NotebookInterface` JSON shape
- run manifest JSON shape
- SSE event shapes
- artifact type tags
- CLI command names

Recommended merge gates:

- Gate 1: storage + interface schema locked
- Gate 2: runtime SDK contract locked
- Gate 3: snapshot API locked
- Gate 4: frontend starts against stable backend contract

**42. Definition Of Done For MVP**

BulletJournal MVP is done when all of the following are true:

- A user can initialize a project with one command.
- A user can start BulletJournal locally with one command and get the graph editor in a browser.
- A user can add file input and notebook nodes.
- A user can connect compatible ports only.
- Notebook ports are derived from strict AST parsing of notebook source.
- Notebook edits update the graph after save without manual refresh.
- Artifact states transition correctly between ready, stale, and pending.
- Managed runs persist artifacts, lineage, and warnings.
- Partial outputs survive notebook failures.
- A project reopens correctly after backend restart.
- Checkpoints can be created and restored.
- The artifact explorer works for basic object, DataFrame, and file artifacts.
- Interactive `Edit & Run` works with explicit heuristic lineage marking.
- The README quickstart is sufficient for a new user to get running without advanced setup.
- The implementation has automated tests for storage, parser, execution, API, and critical failure modes.

**43. Post-MVP Roadmap**

After MVP, prioritize in this order:

- cell-level invalidation and targeted notebook execution
- direct full-path artifact pulls with invisible edges
- pipeline templates
- richer artifact previews
- stronger cache eviction policies
- persistent event log and resumable SSE
- container-based per-project isolation
- RBAC and multi-user studies
- AI integration

**44. Final Guidance**

The most important implementation discipline is separation of concerns.

Do not let:

- the frontend compute lineage
- the runtime module know about HTTP
- the parser write directly to graph files
- Marimo internals leak across the codebase
- the SQLite schema drift informally
- local convenience features weaken the storage contract

If there is a tradeoff between speed and correctness, prefer correctness in storage, lineage, and stale-state semantics. UI convenience can be iterated later; broken reproducibility cannot.