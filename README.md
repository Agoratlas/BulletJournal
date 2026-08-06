# BulletJournal

BulletJournal is a notebook orchestration platform for reproducible data science.
It layers explicit artifact passing, persistent graph state, stale detection, checkpoints,
and managed execution on top of Marimo notebooks.

For multi-project orchestration with separated environments through Docker containers, see
[BulletJournal-Controller](https://github.com/Agoratlas/BulletJournal-Controller).

**DISCLAIMER**: This project is part of an experiment to evaluate the potential of AI tooling for software engineering. Most of the code in this repo was produced by an LLM and may not offer the same security or robustness as human-written code. Please don't deploy it in a critical production environment without isolation, especially given that the project was made to run user-provided Python code.

## Requirements

- Python 3.11+
- A dedicated environment is recommended (`uv` or `venv`)

## Quickstart

```bash
pip install bulletjournal-editor
bulletjournal init testproject
bulletjournal init /project --project-id study-a --skip-environment
bulletjournal start testproject --open
```

If you are already inside a project root, running `bulletjournal` with no subcommand starts the app.

## Common commands

```bash
bulletjournal init testproject
bulletjournal init testproject --project-id custom-id
bulletjournal init /project --project-id study-a --skip-environment
bulletjournal start .
bulletjournal dev . --open
bulletjournal doctor .
bulletjournal validate-templates
bulletjournal rebuild-state .
bulletjournal mark-environment-changed . --reason "dependencies updated"
bulletjournal export . testproject.zip
bulletjournal import testproject.zip restored-study
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
├─ objects/
├─ dashboards/
├─ metadata/
│  ├─ project.json
│  └─ state.db
├─ checkpoints/
├─ temp/
│  ├─ uploads/
│  ├─ execution_logs/
│  └─ worker/
├─ pyproject.toml
└─ uv.lock
```

BulletJournal owns the project layout and schema files under `graph/`, `metadata/`, `objects/`, `dashboards/`, `checkpoints/`, and `temp/`.

`pyproject.toml` and `uv.lock` define the project environment, but they can be managed externally. Use `bulletjournal init ... --skip-environment` when another controller is responsible for environment definition and install orchestration.

`bulletjournal init` is safe to rerun. It leaves valid existing layout files in place, recreates missing required directories and runtime directories, fails on unsupported schema versions, and never overwrites existing `pyproject.toml` or `uv.lock` when `--skip-environment` is used.

## Docs

- `docs/EXECUTION_PLAN.md`
- `docs/ARCHITECTURE.md`
- `docs/PROJECT_FORMAT.md`
- `docs/NOTEBOOK_AUTHORING.md`
- `docs/API.md`
- `docs/TEMPLATES.md`
- `docs/OPERATIONS.md`
- `docs/ARTIFACT_LIFECYCLE.md`
- `docs/TROUBLESHOOTING.md`

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest
```

## Pre-commit

`ruff.toml` is consumed by `ruff` automatically from the repo root. To enable the git hook:

```bash
pre-commit install
```
