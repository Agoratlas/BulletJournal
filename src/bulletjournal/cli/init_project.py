from __future__ import annotations

from pathlib import Path

from bulletjournal.storage import init_project_root


def init_project(path: str, *, title: str | None = None, project_id: str | None = None) -> Path:
    paths = init_project_root(Path(path), title=title, project_id=project_id)
    return paths.root
