# BulletJournal

BulletJournal is a local-first notebook orchestration platform for reproducible data science.
It layers explicit artifact passing, persistent graph state, stale detection, checkpoints,
and managed execution on top of Marimo notebooks.

## What the MVP includes

- FastAPI backend with REST + SSE updates
- Project format rooted in `graph/`, `notebooks/`, `artifacts/`, `metadata/`, and `checkpoints/`
- Strict AST parsing for notebook inputs, outputs, assets, and notebook docs
- Managed notebook execution in subprocesses with persisted artifact lineage and stale propagation
- File input nodes and built-in notebook templates
- Checkpoint create/list/restore flows
- A bundled ReactFlow-based web UI for browsing projects, nodes, artifacts, issues, and events

## Requirements

- Python 3.11+
- A dedicated environment is recommended (`uv` or `venv`)

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

bulletjournal init my-study
cd my-study
bulletjournal start --open
```

If you are already inside a project root, running `bulletjournal` with no subcommand starts the app.

## Common commands

```bash
bulletjournal init my-study
bulletjournal start .
bulletjournal dev . --open
bulletjournal doctor .
bulletjournal validate-templates
bulletjournal rebuild-state .
```

## Project layout

```text
project_root/
├─ graph/
│  ├─ meta.json
│  ├─ nodes.json
│  ├─ edges.json
│  └─ layout.json
├─ notebooks/
├─ artifacts/
│  └─ objects/
├─ metadata/
│  ├─ project.json
│  ├─ environment.json
│  └─ state.db
├─ checkpoints/
└─ uploads/
   └─ temp/
```

## Docs

- `docs/EXECUTION_PLAN.md`
- `docs/ARCHITECTURE.md`
- `docs/PROJECT_FORMAT.md`
- `docs/NOTEBOOK_AUTHORING.md`
- `docs/API.md`
- `docs/TEMPLATES.md`
- `docs/OPERATIONS.md`
- `docs/TROUBLESHOOTING.md`

## Testing

```bash
PYTHONPATH=src python -m pytest
```

## Status

This repository now contains a functional MVP with tested core flows for:

- project init/open
- interface parsing
- graph persistence and validation
- managed notebook runs
- artifact state transitions
- checkpoints and restore
- SSE event streaming and API-driven UI updates

Known MVP constraints remain around deployment hardening and background execution semantics, but the repository is no longer just a skeleton.
