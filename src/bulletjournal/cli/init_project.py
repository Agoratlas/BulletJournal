from __future__ import annotations

from pathlib import Path

from bulletjournal.storage import init_project_root


def init_project(
    path: str,
    *,
    title: str | None = None,
    project_id: str | None = None,
    initialize_environment: bool = True,
) -> Path:
    paths = init_project_root(
        Path(path),
        title=title,
        project_id=project_id,
        initialize_environment=initialize_environment,
    )
    return paths.root
