# Assets Implementation Plan

## Purpose

This document is the main implementation reference for the new BulletJournal assets feature described in `ASSETS_IMPLEMENTATION.md`.

It makes concrete design decisions where the spec intentionally leaves room, and it aligns those decisions with the current BulletJournal architecture:

- project root is the persistence boundary
- SQLite stores mutable metadata
- the shared object store remains the content-addressed storage backend
- notebook interfaces are parsed from source
- notebook execution stays in subprocesses
- viewer state and graph state stay separate unless there is a strong reason to couple them

## Summary Of Final Decisions

1. The deprecated artifact subtype called `asset` is removed completely, with no backwards compatibility and no migration path.
2. Assets are not graph ports and are never connectable through execution edges.
3. Artifact declarations and asset declarations are parsed together in a single AST pass, but asset objects themselves are resolved only at runtime.
4. The runtime public API is `from bulletjournal.runtime import assets`.
5. `asset_type` in the Python push API is an optional asset class reference such as `asset_type=assets.Markdown`, not a string literal.
6. Saved dashboards are persisted as JSON documents under `dashboards/` and represented in the graph by a new `dashboard` node kind.
7. Dashboard membership is not stored in `graph/edges.json`. Dashboard source notebook links live inside each dashboard JSON file.
8. Standalone notebook viewers keep unsaved order and override state in the browser only. The backend only receives the effective modifiers needed to prepare data.
9. Interactive asset data preparation runs in the API process as a bounded read-only query layer. It does not execute user notebook code.
10. Interactive tabular and dataviz data preparation uses Polars on top of the parquet-backed shared object store under `objects/`.
11. The project format change is intentionally breaking: `artifacts/objects/` becomes `objects/`, the `artifact_objects` table becomes `objects`, and the project schema version is bumped to 2. Schema version 1 projects are not supported by the new release.
12. Asset freshness uses the same state vocabulary as artifacts (`pending`, `ready`, `stale`) but is stored in dedicated asset tables.
13. Collections are tracked only at the parent asset level. Child assets are stored inside the parent asset version payload, may be explicitly named, and are not individually freshness-tracked.
14. Pagination is part of the modifier model. Page index and page size are query modifiers, not a separate request-state system.
15. Dashboard viewers use SSE for invalidation and refresh. There is no polling loop.

## High-Level End State

After implementation, BulletJournal will have two distinct output systems.

Artifacts:

- stay responsible for notebook-to-notebook data propagation
- remain part of execution planning and graph compatibility
- continue using the artifact runtime and artifact-specific state tables, while sharing the unified parser and shared object store

Assets:

- are notebook-produced analyst-facing outputs
- can be viewed in a standalone notebook dashboard or in saved dashboards
- are stored in dedicated asset metadata tables plus the shared object store
- are never valid graph source ports
- have their own parsing, runtime validation, API routes, and frontend viewer

## Phase 0: Breaking Reset

This must happen before the new asset implementation to avoid name collisions and misleading partial reuse.

### Code Removal Scope

Delete the current use of `ArtifactRole.ASSET`, `NotebookInterface.assets`, and all frontend/backend logic that treats assets as artifact outputs.

Primary files to update:

- `src/bulletjournal/domain/enums.py`
- `src/bulletjournal/domain/models.py`
- `src/bulletjournal/parser/interface_parser.py`
- `src/bulletjournal/runtime/context.py`
- `src/bulletjournal/runtime/standalone.py`
- `src/bulletjournal/services/notebook_service.py`
- `src/bulletjournal/services/graph_service.py`
- `src/bulletjournal/services/run_service.py`
- `src/bulletjournal/services/artifact_service.py`
- `src/bulletjournal/services/checkpoint_service.py`
- `src/bulletjournal/services/project_service.py`
- `web/src/lib/types.ts`
- `web/src/lib/helpers.ts`
- `web/src/App.tsx`
- `web/src/components/GraphCanvas.tsx`
- `web/src/components/NodeInspector.tsx`

### Breaking-Change Strategy

There is no compatibility layer for the deprecated feature.

Decision:

- delete the old runtime, parser, DB, service, and UI handling directly
- remove all code that attempts to read or interpret legacy artifact-side assets
- do not add migration code for historical `role = 'asset'` rows or legacy `interface.assets` payloads
- fail fast when opening an old schema version 1 project
- create the new release around schema version 2 only
- do not support mixed-mode projects where old and new asset systems coexist

### Project Schema Reset

The new release introduces a new on-disk project format.

Decision:

- `metadata/project.json.schema_version` is bumped to `2`
- `graph/meta.json.schema_version` is bumped as needed when the `dashboard` node kind is introduced
- the shared object store directory becomes `objects/`
- the DB catalog table `artifact_objects` becomes `objects`
- schema version 1 support is removed from project loading, archive import, and rebuild flows

### Acceptance Criteria

- no runtime path references `ArtifactRole.ASSET`
- no parser output contains `interface.assets`
- assets are no longer shown in notebook output port lists or graph connection logic
- schema version 1 projects are rejected explicitly and immediately
- new schema version 2 projects contain no trace of the deprecated feature

## New Public Runtime API

Create a new runtime module:

- `src/bulletjournal/runtime/assets.py`

Expose it from:

- `src/bulletjournal/runtime/__init__.py`

The public notebook authoring form becomes:

```python
with app.setup:
    from bulletjournal.runtime import artifacts, assets
```

### Public API Surface

The runtime module should expose:

- `assets.push(asset, *, name, title, description=None, asset_type: type[BaseAsset] | None = None)`
- `assets.Markdown(text)`
- `assets.HTML(html)`
- `assets.DataFrame(df)`
- `assets.Collection(display='all' | 'single')`
- `collection.add_asset(child, name=None)`
- dataviz asset classes for later phases

If `collection.add_asset(...)` omits a name, the child name is assigned automatically as `asset_1`, `asset_2`, and so on.

### Authoring Rules

`assets.push(...)` follows the same parser constraints as `artifacts.push(...)`:

- direct top-level call only
- no aliasing
- no wrappers
- no loops or conditionals around the push call
- `name`, `title`, and `description` must be literals
- optional `asset_type` must be a direct asset class reference such as `assets.Markdown`

Asset object construction is intentionally less constrained.

Decision:

- asset constructors may run in arbitrary notebook code
- collections may be built dynamically
- only the parent asset pushed by `assets.push(...)` is statically declared

### `asset_type` Semantics

The optional `asset_type` argument is a Python asset class reference used for declaration-time metadata and runtime validation.

Decision:

- if omitted, the declaration records `declared_asset_type = null`
- if present, the parser canonicalizes the class reference to the internal asset type id
- only direct `assets.<ClassName>` references are accepted in source
- at runtime, every pushed asset resolves to a concrete runtime type id
- if `asset_type` is provided and does not exactly match the runtime type id, the run fails

Canonical asset type ids:

- `generic`
- `markdown`
- `html`
- `image`
- `dataframe`
- `histogram`
- `timeseries_histogram`
- `pie_chart`
- `bar_chart`
- `line_plot`
- `scatter_plot`
- `collection`

## Parser And Declaration Model

Do not overload artifact graph-port semantics for new assets.

Decision:

- keep artifact interface data and asset declaration data as separate models
- produce both models from a single unified parser pass over the notebook source

### New Domain Models

Add new domain dataclasses similar in style to the existing models.

Suggested additions in `src/bulletjournal/domain/models.py` or a dedicated `asset_models.py`:

- `ParsedNotebookContract`
- `AssetDeclaration`
- `AssetHead`
- `AssetVersionSummary`
- `DashboardDocument`
- `DashboardPanel`

`ParsedNotebookContract` fields:

- `source_hash: str`
- `docs: str | None`
- `issues: list[ValidationIssue]`
- `interface: NotebookInterface`
- `asset_declarations: list[AssetDeclaration]`

`AssetDeclaration` fields:

- `node_id: str`
- `name: str`
- `title: str`
- `description: str | None`
- `declared_asset_type: str | None`
- `declaration_index: int`

### Parser Work

Extend the parser package with a unified notebook contract parser.

Suggested implementation shape:

- replace `parse_notebook_interface(...)` with `parse_notebook_contract(...)`
- walk the AST once and collect artifact interface data, asset declarations, issues, docs, and source hash together
- stop duplicating source parsing and AST traversal between artifact and asset extraction

Accepted syntax:

```python
assets.push(obj, name='my_asset', title='My asset')
assets.push(obj, name='my_asset', title='My asset', description='...', asset_type=assets.Markdown)
```

Rejected syntax mirrors artifact rules.

### Notebook Reparse Behavior

On notebook save or rebuild:

- parse the unified notebook contract in one pass
- replace stored declarations for the notebook atomically
- create missing `asset_heads` rows as `pending`
- delete removed asset state rows

If the notebook has parse errors:

- preserve the last valid parsed contract, exactly like current invalid notebook handling preserves the last valid interface for artifacts

## SQLite Data Model

Asset metadata is persisted in `metadata/state.db`.

### New Tables

Define the following tables in the new schema version 2 state database.

#### `asset_declarations`

Stores the latest parsed declaration set per notebook.

Columns:

- `node_id TEXT NOT NULL`
- `asset_name TEXT NOT NULL`
- `title TEXT NOT NULL`
- `description TEXT NULL`
- `declared_asset_type TEXT NULL`
- `declaration_index INTEGER NOT NULL`
- `source_hash TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Primary key:

- `(node_id, asset_name)`

#### `asset_versions`

Stores immutable produced asset versions.

Columns:

- `asset_version_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `node_id TEXT NOT NULL`
- `asset_name TEXT NOT NULL`
- `asset_type TEXT NOT NULL`
- `interactive INTEGER NOT NULL`
- `source_hash TEXT NOT NULL`
- `upstream_code_hash TEXT NOT NULL`
- `upstream_data_hash TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `lineage_mode TEXT NOT NULL`
- `definition_json TEXT NOT NULL`
- `modifier_schema_json TEXT NOT NULL`
- `default_modifiers_json TEXT NOT NULL`
- `override_schema_hash TEXT NOT NULL`
- `warning_json TEXT NOT NULL DEFAULT '[]'`
- `created_at TEXT NOT NULL`

`definition_json` stores the immutable asset definition only. It must never include dashboard overrides.

#### `asset_heads`

Tracks the current version and freshness state for each logical asset.

Columns:

- `node_id TEXT NOT NULL`
- `asset_name TEXT NOT NULL`
- `current_asset_version_id INTEGER NULL`
- `state TEXT NOT NULL`

Primary key:

- `(node_id, asset_name)`

State vocabulary:

- `pending`
- `ready`
- `stale`

#### `asset_version_objects`

Maps asset versions to shared object-store payloads.

Columns:

- `asset_version_id INTEGER NOT NULL`
- `object_role TEXT NOT NULL`
- `object_index INTEGER NOT NULL DEFAULT 0`
- `artifact_hash TEXT NOT NULL`
- `metadata_json TEXT NULL`

Primary key:

- `(asset_version_id, object_role, object_index)`

Allowed `object_role` values:

- `backing_dataset`
- `static_view`
- `image_blob`
- `attachment`

This solves the multi-object requirement cleanly while reusing the shared object store.

### Why Definitions Live In SQLite

This is the best fit for the spec and the existing architecture.

- definitions are structured, queryable metadata
- dashboards need fast metadata joins without loading blobs
- asset definitions are much smaller than backing datasets
- the object store remains reserved for heavy immutable payloads

## Shared Object Store Usage

The canonical content-addressed blob store for both artifacts and assets lives under `objects/`.

Decision:

- rename the on-disk path from `artifacts/objects/` to `objects/`
- rename the DB catalog table from `artifact_objects` to `objects`
- remove schema version 1 loading support instead of adding migration compatibility
- keep the existing `artifact_hash` field name in artifact-facing tables for this release to avoid unrelated churn beyond the shared catalog rename
- treat it as the shared immutable object catalog for the whole application

### Deduplication Rules

Interactive backing datasets must be persisted via the existing object-store serializer path so deduplication happens automatically.

That means:

- if an artifact and an asset persist the same DataFrame bytes, they share one `artifact_hash`
- if multiple assets persist the same DataFrame bytes, they share one `artifact_hash`
- deduplication is by serialized bytes only

## Asset Version Structure

Every produced asset version is composed of three immutable layers.

### 1. Definition

Stored in `asset_versions.definition_json`.

Contains:

- concrete `asset_type`
- presentation configuration encoded by the notebook
- references to child definitions for collections
- references by logical role to object-store payloads
- any static content needed for rendering

### 2. Backing Dataset

Optional. Stored in the object store and linked through `asset_version_objects`.

Used for:

- interactive DataFrame assets
- interactive dataviz assets
- collection children that are themselves interactive

### 3. Default Modifiers

Stored in `asset_versions.default_modifiers_json`.

This is part of the immutable asset version. Dashboard changes never mutate it.

## Asset Definition Schema

Use JSON-serializable asset definitions. Every asset definition includes the shared base fields:

- `asset_type`
- `interactive`
- `display_title`
- `description`
- `supports_table_view`
- `modifier_defaults`
- `modifier_schema`
- `interaction_bindings`
- `data_dependencies`

### Markdown Definition

Fields:

- `asset_type: "markdown"`
- `markdown_text`

### HTML Definition

Fields:

- `asset_type: "html"`
- `html_text`

Decision:

- HTML is rendered with `dangerouslySetInnerHTML`
- no sanitization layer is added because the trust model is explicitly trusted-user only
- scripts remain blocked by normal browser behavior inside injected HTML unless explicitly allowed by the renderer implementation
- do not permit arbitrary external JS execution in the first implementation

### Image Definition

Fields:

- `asset_type: "image"`
- `mime_type`
- `object_role: "image_blob"`

### DataFrame Definition

Fields:

- `asset_type: "dataframe"`
- `interactive: true`
- `table_columns`
- `row_count`
- `object_role: "backing_dataset"`

### Dataviz Definition

Shared fields:

- `asset_type`
- `interactive`
- `dataset_binding`
- `encodings`
- `visual_defaults`
- `vega_template_id` or `vega_template_kind`

Decision:

- do not persist raw user-authored Vega-Lite specs as the core asset contract
- persist a BulletJournal-owned normalized chart definition, then compile it to Vega-Lite in the backend or frontend renderer

This keeps runtime behavior under BulletJournal control and avoids making the stored asset contract depend on ad hoc Vega details.

### Collection Definition

Fields:

- `asset_type: "collection"`
- `display_mode_default: "all" | "single"`
- `children: [...]`

Decision:

- children are stored inline inside the parent definition
- collections cannot contain collections
- child names are unique within the collection
- `collection.add_asset(child, name='...')` assigns an explicit child name
- omitted child names default to `asset_1`, `asset_2`, and so on in insertion order
- child freshness is not stored separately

## Modifier Model

Modifiers are the only mutable input to asset rendering after an asset version exists.

### Modifier Implementation Layers

Assets expose modifiers through two complementary declaration mechanisms.

#### Settings Modifiers

These power the common panel `Settings` UI.

Characteristics:

- declared in a restricted structured format
- intended for simple controls shared by many asset types
- supported scalar forms are `int`, `float`, `bool`, and `enum`
- examples: title visibility, font size, line thickness, histogram bin count, page size

Every asset type should have a simple way to declare these settings modifiers in its Python definition layer and surface them automatically in the frontend settings menu.

#### Custom Interaction Modifiers

These are more flexible modifiers driven by component-specific UI interactions.

Characteristics:

- declared as normalized modifier definitions plus an event-to-modifier adapter
- may be generated from table UI actions, custom widgets, or Vega signal listeners
- examples: drag-to-select a range in a chart, click a legend category, brush a scatter region

For dataviz assets, the React component receives raw Vega signal events and converts them into normalized modifier updates before sending them through the same modifier pipeline as settings modifiers. The backend never receives raw Vega signal payloads as a public contract.

### Modifier Categories

Every modifier must be classified into one of these categories.

#### `saved_query`

- persisted in saved dashboard panels
- changes the prepared backend data
- includes query-navigation state such as page index and page size
- examples: filters, sorting, page index, page size, histogram bin count

#### `saved_view`

- persisted in saved dashboard panels
- changes rendering only in the browser
- examples: title visibility, font size, line thickness

#### `transient_view`

- never persisted
- reset when the underlying prepared data changes
- examples: chart legend highlight, drag selection highlight

The category names describe lifecycle and persistence semantics, not where the code runs.

### Pagination And Navigation

Pagination is not a separate state system. It is part of the modifier model.

Decision:

- page index and page size are `saved_query` modifiers
- in standalone viewers they remain unsaved browser state, but they are still sent as effective modifiers to the prepare endpoint
- in saved dashboards they are persisted like other query modifiers
- resetting query modifiers resets pagination as well unless the action explicitly changes only the page number

### Modifier Schema Format

Each asset version stores a normalized modifier schema array.

Each modifier record includes:

- `id`
- `title`
- `kind`
- `category`
- `server_targets`
- `default_value`
- `enabled_when`
- `options` when enum-based

Allowed `kind` values:

- `int`
- `float`
- `bool`
- `enum`
- `page`
- `sort`
- `filter_range`
- `filter_value_set`
- `filter_regex`

Allowed `server_targets` values:

- `[]`
- `["main"]`
- `["table"]`
- `["main", "table"]`

### Override Compatibility

Each asset version stores `override_schema_hash`.

Saved dashboard panels also persist the last accepted `override_schema_hash`.

Decision:

- when a panel loads and its stored overrides no longer match the current asset version's schema hash, the backend attempts a best-effort validation
- if overrides are invalid, the panel enters `override_incompatible` state
- the UI offers `Reset panel overrides`

This is how the spec's update-handling requirement is implemented without guessing blindly.

## Runtime Persistence Flow

Implement a dedicated asset push path in the runtime context.

### During Managed Notebook Execution

The run manifest must include asset declarations in addition to artifact outputs.

Suggested manifest addition:

- `assets: {name -> declaration metadata}`

When `assets.push(...)` is called:

1. validate the asset name exists in the parsed declaration set
2. validate title and description match the parsed declaration
3. resolve the concrete asset type from the runtime asset object
4. validate optional declared `asset_type`
5. serialize the asset definition and any backing object references
6. persist heavy payloads in the shared object store
7. create a new `asset_versions` row
8. update `asset_heads`
9. publish asset SSE events

### During Standalone Notebook Execution

Mirror the managed runtime behavior using the standalone runtime context.

Decision:

- standalone notebook execution persists assets exactly like artifacts do today
- downstream asset staleness is computed using the graph and input artifact state once freshness tracking is implemented

## Freshness And Lineage

The final system should track asset freshness independently from artifacts.

### Asset Freshness Rules

- a newly declared but never produced asset is `pending`
- a produced asset is `ready`
- if notebook source changes, the asset becomes `stale`
- if any upstream input artifact changes, the asset becomes `stale`
- if a notebook runs using stale inputs, produced assets are immediately persisted as `stale`

### Hash Strategy

Reuse the existing artifact lineage pattern conceptually, but store the result in dedicated asset tables.

For each asset version compute:

- `source_hash`
- `upstream_code_hash`
- `upstream_data_hash`

Calculation rule:

- include notebook source hash
- include logical asset id `node_id/asset_name`
- include upstream artifact lineage hashes

Do not include dashboard overrides. Dashboards must never affect asset freshness.

### Collection Freshness

Decision:

- collections are stale if the collection asset version is stale
- there is no child-level freshness table
- child changes only matter through the parent collection version that produced them

## Data Preparation Layer

This is the core backend layer used by interactive assets.

### Scope

The preparation layer:

- reads immutable backing datasets from the object store
- applies validated modifiers
- returns bounded prepared payloads for the frontend
- never executes notebook code

### Why It Can Run In The API Process

This does not violate the architecture rule about runs happening in subprocesses, because asset preparation is not notebook execution. It is an internal deterministic query service over immutable stored data.

### Engine Choice

Add `polars` as a Python dependency.

Decision:

- notebooks still produce pandas DataFrames normally
- storage remains parquet bytes in the shared object store
- preparation uses Polars scans over the stored parquet payloads

### Initial Prepared Payload Targets

Prepared payload targets are:

- `main`
- `table`

Examples:

- DataFrame asset only uses `table`
- interactive histogram uses both `main` and `table`
- non-interactive markdown uses neither

### Data Size Limits

Apply explicit limits.

Decision:

- target response size under 300 kB for normal prepared payloads
- hard cap response body at 1 MB for any single asset prepare response
- hard cap table page sizes to configured options only
- hard cap value-filter distinct option enumeration to 30 items plus `others` and `empty`

### Backend Module Layout

Suggested new package:

- `src/bulletjournal/assets/`

Suggested modules:

- `definitions.py`
- `runtime_types.py`
- `serializer.py`
- `prepare/base.py`
- `prepare/dataframe.py`
- `prepare/dataviz.py`
- `prepare/modifiers.py`
- `dashboard_models.py`

## Dashboard Persistence Model

Saved dashboards are durable project files.

### New Project Directory

Add:

- `dashboards/`

The canonical project layout also changes:

- remove `artifacts/objects/`
- add `objects/`

Update:

- `src/bulletjournal/storage/project_fs.py`
- `docs/PROJECT_FORMAT.md`
- export/import logic
- project initialization and validation

### Dashboard Graph Node

Add a new node kind:

- `dashboard`

The graph stores:

- node id
- title
- layout
- lightweight UI metadata if needed

The graph does not store panel definitions or notebook membership.

### Dashboard JSON Schema

Persist each dashboard as `dashboards/<dashboard_id>.json`.

Recommended schema:

```json
{
  "schema_version": 1,
  "dashboard_id": "dashboard_eval",
  "version": 3,
  "title": "Evaluation dashboard",
  "created_at": "...",
  "updated_at": "...",
  "sources": [
    { "node_id": "train_eval" },
    { "node_id": "error_analysis" }
  ],
  "panels": [
    {
      "panel_id": "train_eval/example_hist",
      "node_id": "train_eval",
      "asset_name": "example_hist",
      "visible": true,
      "position": 0,
      "modifier_overrides": {},
      "override_schema_hash": "..."
    }
  ]
}
```

### Dashboard Versioning

Every patch request must provide `dashboard_version`.

Decision:

- if the incoming version does not match the stored version, return HTTP 409
- the response includes the latest stored dashboard payload so the client can rebase

### Dashboard Membership Representation

Even though the spec calls these links "dashboard edges", they must not use `graph/edges.json` because those edges are execution semantics.

Decision:

- store notebook membership in `sources`
- render them as pseudo-edges in the editor only when a dashboard node is selected

### Dashboard Block Templates

Dashboard blocks may appear inside pipeline templates and may pre-populate their own dashboard configuration.

Decision:

- dashboard blocks are supported only inside pipeline templates
- there is no standalone dashboard template asset type
- pipeline templates may define dashboard source notebooks, panel order, visibility, and modifier overrides
- when the pipeline is instantiated, dashboard source notebook ids are remapped from template-local node ids to the actual instantiated notebook ids
- dashboard JSON files are materialized at pipeline creation time from the template definition

Suggested pipeline template support:

- allow `kind: "dashboard"` in pipeline template node definitions
- store dashboard template content under a dedicated field such as `dashboard` on that node definition
- validate that every referenced source notebook id exists within the same pipeline template
- validate that panel references are unique per dashboard template
- validate modifier overrides against the declared panel asset reference format as far as possible before instantiation

Suggested dashboard template payload shape:

```json
{
  "sources": [
    { "node_id": "eval_notebook" }
  ],
  "panels": [
    {
      "panel_id": "eval_notebook/example_hist",
      "node_id": "eval_notebook",
      "asset_name": "example_hist",
      "visible": true,
      "position": 0,
      "modifier_overrides": {}
    }
  ]
}
```

## API Design

The current artifact API is not reused for assets except for shared object downloading where appropriate.

### New Asset Endpoints

Add routes under `/api/v1`.

#### Notebook Asset Listing

- `GET /nodes/{node_id}/assets`

Returns:

- parsed declarations
- current head state per asset
- current version metadata if present

#### Single Asset Metadata

- `GET /assets/{node_id}/{asset_name}`

Returns:

- declaration metadata
- current head metadata
- asset version summary
- modifier schema summary

#### Prepared View

- `POST /assets/{node_id}/{asset_name}/prepare`

Request body:

- `asset_version_id` optional optimistic check
- `modifier_overrides`
- `transient_modifiers`
- `panel_context` optional

Response body:

- `asset_version_id`
- `state`
- `resolved_modifiers`
- `override_schema_hash`
- `payloads`
- `errors` if overrides became invalid

### New Dashboard Endpoints

#### Saved Dashboard CRUD

- `GET /dashboards/{dashboard_id}`
- `POST /dashboards`
- `PATCH /dashboards/{dashboard_id}`
- `DELETE /dashboards/{dashboard_id}`

#### Save Standalone Viewer As Dashboard

- `POST /nodes/{node_id}/dashboards`

Request body includes the standalone panel order and overrides. The server creates:

- a `dashboard` graph node
- a `dashboards/<dashboard_id>.json` file
- a default layout position a few grid steps above the source notebook

### SSE Events

Add asset and dashboard events.

Suggested event names:

- `asset.state_changed`
- `asset.version_created`
- `dashboard.updated`
- `dashboard.deleted`

Decision:

- the dashboard viewer listens to SSE for low-latency invalidation
- there is no polling fallback loop
- asset and dashboard views refetch only in response to relevant SSE events or direct user actions

## Frontend Architecture

Assets are viewed outside the main editor canvas, but still inside the same bundled SPA.

### Route Strategy

Do not add a routing library unless it is clearly needed.

Decision:

- keep the existing single-SPA approach
- inspect `window.location.pathname`
- render one of three top-level modes:
  - graph editor
  - standalone notebook dashboard viewer
  - saved dashboard viewer

Suggested paths:

- `/` for the main editor
- `/notebooks/{node_id}/assets` for standalone viewer
- `/dashboards/{dashboard_id}` for saved dashboards

### New Frontend Areas

Suggested modules:

- `web/src/assets/`
- `web/src/dashboard/`

Suggested components:

- `DashboardPage`
- `DashboardSidebar`
- `AssetPanel`
- `PanelHeader`
- `AssetRenderer`
- `MarkdownAssetPanel`
- `HtmlAssetPanel`
- `DataFrameAssetPanel`
- `CollectionAssetPanel`
- `DatavizAssetPanel`

Suggested state modules:

- `dashboardTypes.ts`
- `dashboardApi.ts`
- `dashboardReducer.ts`

### Panel State Model

Each panel maintains:

- asset identity
- current version id
- visibility
- persisted overrides
- transient modifiers
- fetch status per target (`main`, `table`)
- override compatibility state

### Data Refresh Rules

When a panel modifier changes:

- update local state immediately
- if the modifier affects a server target, debounce and request a new prepared payload
- only show loading indicators for the affected targets

When a relevant SSE event arrives:

- refetch the affected asset metadata or dashboard document
- invalidate prepared payloads for panels referencing the changed asset version
- keep saved overrides, clear transient view modifiers, and re-prepare as needed

When the asset version changes:

- keep persisted overrides
- clear transient modifiers
- refetch prepared data
- if overrides are invalid, show the reset CTA

### Standalone Viewer Behavior

Decision:

- standalone viewers keep panel order, visibility, and overrides only in browser state
- no backend persistence exists for standalone dashboards
- asset prepare requests include the current effective modifiers on each call
- closing the tab loses local changes by design

This is simpler and fits the unsaved nature of the standalone viewer.

### Saved Dashboard Behavior

Decision:

- saved dashboards persist order, visibility, and persisted modifier overrides to `dashboards/<dashboard_id>.json`
- PATCH requests update the dashboard version atomically

### Editor Integration

In the main editor:

- notebooks get a new `View assets` action
- dashboard nodes get an `Open dashboard` action
- when a dashboard node is selected, pseudo-links to source notebooks are rendered visually. These pseudo-links go from the top-center of the notebook to the bottom-center of the dashboard node. 

## DataFrame Asset Details

This is the first interactive asset and the foundation for later dataviz panels.

### Backend Preparation Rules

Support in MVP:

- pagination
- column sorting

Support later:

- numeric/date range filters
- value filters
- regex filters
- sampling

### Query Rules

Decision:

- page index is zero-based in the API and converted as needed in the UI
- sort state is an ordered list, but MVP supports exactly one active sort key
- null sort order is `nulls_last` for ascending and descending in MVP for predictability
- neighboring pages may be prefetched by the frontend, but the backend API remains page-based and stateless

### Prepared Table Payload Shape

Recommended response shape:

```json
{
  "kind": "table",
  "rows_total": 1234,
  "columns": [
    { "id": "col_a", "title": "col_a", "data_type": "int64", "sortable": true }
  ],
  "page": { "index": 0, "size": 10 },
  "sort": [
    { "column": "col_a", "direction": "asc" }
  ],
  "rows": [
    { "col_a": 1 }
  ]
}
```

## Dataviz Asset Details

Dataviz is not in MVP, but the full design should already shape the APIs and data model.

### Rendering Strategy

Add frontend dependencies later:

- `vega`
- `vega-lite`
- `vega-embed`

Decision:

- the frontend renders Vega-Lite views
- the backend prepares condensed datasets and compiled view input
- the backend does not emit huge raw tables to the browser

### Dataviz Modifier Declaration

Dataviz panels use both modifier declaration mechanisms described earlier.

#### Common Settings

Each dataviz asset type exposes a structured list of settings modifiers for the shared panel settings menu.

Characteristics:

- restricted to `int`, `float`, `bool`, and `enum`
- easy to declare in Python alongside the asset definition
- rendered automatically by the frontend settings UI
- examples: line width, axis visibility, legend visibility, density overlay toggle, bar orientation

#### Custom Interactions

Dataviz panels may also declare interaction bindings that convert UI events into normalized modifiers.

Characteristics:

- defined per asset type in the frontend renderer
- backed by explicit modifier ids already present in the asset definition schema
- may be persisted or transient depending on the modifier category
- examples: brushed X range, clicked legend category, dragged scatter selection rectangle

For Vega-based panels, a signal listener translates Vega output into a normalized modifier payload. The rest of the system only sees the normalized modifier id and value.

### Interactive Rules

There are two distinct state channels.

#### Saved query modifiers

- filters from the DataFrame viewer
- sort and sampling
- pagination
- histogram bin count
- these affect prepared backend data

#### Transient view modifiers

- legend highlights
- brushed range highlights that should not change the chart's prepared dataset
- these affect only the frontend rendering and the linked table focus

This directly implements the behavior described in the spec.

## Collections

Collections are a container asset, not a group of independently tracked assets.

### Runtime Representation

- `assets.Collection()` holds child asset objects
- `collection.add_asset(child, name='...')` optionally assigns a child name
- the pushed asset name identifies the whole collection
- the collection serializer expands child definitions inside `definition_json`

### Viewer Representation

Panel display modes:

- `all`
- `single`

In `single` mode the panel stores the selected child name as a `saved_view` override.

### Freshness Decision

- only the collection head is tracked
- child-level freshness is intentionally absent

## Services Layer Plan

Add dedicated services instead of extending `ArtifactService` with unrelated logic.

### New Services

- `AssetService`
- `DashboardService`
- `AssetPrepareService`

### Responsibilities

`AssetService`:

- declaration lookup
- asset version creation
- asset head reads and updates
- asset listing by notebook
- staleness transitions

`AssetPrepareService`:

- validate modifier input
- load backing datasets
- compute prepared payloads

`DashboardService`:

- read/write dashboard JSON files
- version conflict handling
- create dashboard nodes
- save standalone viewer state as dashboard

## Project Filesystem Changes

Update `ProjectPaths` with:

- `dashboards_dir`
- `dashboard_path(dashboard_id)`
- `objects_dir`

Update project initialization and validation so `dashboards/` and `objects/` are always present.

Update export/import/archive support so dashboard files are included.

## Snapshot And Main Editor Changes

Keep the main snapshot compact.

Decision:

- do not inline full dashboard documents in `/project/snapshot`
- the snapshot only needs dashboard graph nodes like other nodes
- dashboard document contents are fetched through dedicated endpoints when needed

Needed snapshot changes:

- add `dashboard` to node kind unions
- ensure dashboard nodes appear in graph payloads

## Testing Strategy

### Python Unit Tests

Add unit tests for:

- asset declaration parsing
- unified contract parsing with artifacts and assets together
- invalid asset declaration patterns
- runtime asset type validation
- asset DB lifecycle
- object-store reference linking
- DataFrame preparation logic
- dashboard file read/write/version conflict behavior

### Python Integration Tests

Add integration tests for:

- notebook run produces asset version rows
- notebook reparse adds and removes declarations correctly
- stale input artifact causes stale asset head
- saved dashboard opens and patches correctly
- dashboard save creates graph node plus JSON file
- pipeline template instantiation materializes dashboard blocks with remapped source notebook ids

### API Tests

Add API tests for:

- `GET /nodes/{node_id}/assets`
- `POST /assets/{node_id}/{asset_name}/prepare`
- dashboard CRUD endpoints
- dashboard version conflict responses

### Frontend Tests

Add a frontend test harness if not already present.

Recommended additions:

- `vitest`
- `@testing-library/react`
- `jsdom`

Add tests for:

- dashboard reducer state transitions
- DataFrame panel pagination and sorting controls
- override incompatibility UI
- standalone viewer unsaved behavior

## Documentation Updates

Update the following docs as implementation lands:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/PROJECT_FORMAT.md`
- `docs/API.md`
- `docs/NOTEBOOK_AUTHORING.md`
- `docs/TEMPLATES.md`

Important documentation changes:

- new runtime import examples for `assets`
- removal of the old artifact asset subtype references
- new project layout including `dashboards/` and `objects/`
- new dashboard and assets API endpoints
- dashboard block support inside pipeline templates

## Recommended Implementation Order

This order minimizes rework.

### Phase 0

- remove deprecated artifact-asset support
- drop schema version 1 support entirely
- rename `artifacts/objects/` to `objects/`
- rename `artifact_objects` to `objects`
- update tests and frontend types accordingly

### Phase 1

- add runtime `assets` module
- add unified parser for artifact interfaces and asset declarations
- add new state DB tables and service primitives
- persist asset versions from notebook runs
- expose notebook asset listing API

### Phase 2

- add standalone dashboard viewer route
- implement `Markdown` and `DataFrame` asset renderers
- implement DataFrame preparation backend with Polars
- add prepare endpoint
- add notebook `View assets` action

### Phase 3

- add `dashboard` node kind
- add dashboard JSON persistence
- add dashboard CRUD API
- add `Save as dashboard`
- add dashboard block support in pipeline templates
- add editor pseudo-links when dashboard is selected

### Phase 4

- implement full modifier model
- implement update conflict handling and override incompatibility states
- add SSE-based invalidation and refresh
- add sidebar, order, visibility persistence

### Phase 5

- add dataviz assets
- add linked table plus chart interactions
- add collection panels
- add freshness tracking for assets in the UI

## Explicit Non-Goals For Early Phases

Do not do these before the foundations above are in place.

- do not reuse `ArtifactService` as the primary home for new assets
- do not store dashboard membership in `graph/edges.json`
- do not make assets connectable ports
- do not implement dataviz before the DataFrame viewer contract is stable
- do not add server-side caching for prepared payloads before the uncached path works

## MVP Scope

The MVP is intentionally smaller than the full design above.

### Included

- removal of the deprecated artifact-asset subtype
- removal of schema version 1 project support
- new runtime `assets` module with `assets.push(...)`
- unified parsing of artifact interfaces and asset declarations in notebook source
- persisted asset metadata and asset versions in SQLite
- shared object-store usage for backing datasets
- standalone notebook asset viewer only
- `Markdown` asset type
- `DataFrame` asset type
- reusable DataFrame viewer component
- backend DataFrame preparation with Polars LazyFrame queries
- pagination for DataFrame assets
- single-column sorting for DataFrame assets
- notebook-level `View assets` action in the main editor
- Python unit and integration tests for all implemented backend features
- frontend component/reducer tests for the new viewer if the test harness is added in the same phase

### Excluded

- no saved dashboards
- no `dashboard` graph node kind
- no `dashboards/` directory usage in MVP code paths
- no sidebar
- no asset freshness tracking
- no SSE asset events required for MVP
- no dataviz assets
- no collection assets
- no dashboard block templates
- no DataFrame filtering or sampling yet
- no HTML or image asset types yet
- no panel override persistence beyond in-tab standalone viewer state

### MVP Acceptance Criteria

- a notebook can declare assets using `assets.push(...)`
- running the notebook creates asset records visible through the new API
- the main editor offers `View assets` for notebooks
- opening the standalone viewer shows all current notebook assets in declaration order
- Markdown panels render correctly
- DataFrame panels render correctly with pagination and sorting
- large DataFrames are prepared through the backend rather than shipped whole to the browser
- all obsolete artifact-asset behavior has been removed from backend and frontend code
