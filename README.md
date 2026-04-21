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
bulletjournal start testproject --open
```

If you are already inside a project root, running `bulletjournal` with no subcommand starts the app.

## Common commands

```bash
bulletjournal init testproject
bulletjournal init testproject --project-id custom-id
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
├─ artifacts/
│  └─ objects/
├─ metadata/
│  ├─ project.json
│  └─ state.db
├─ checkpoints/
├─ uploads/
│  └─ temp/
├─ pyproject.toml
└─ uv.lock
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