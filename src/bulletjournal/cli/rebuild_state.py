from __future__ import annotations

from pathlib import Path

from bulletjournal.api.deps import ServiceContainer


def rebuild_state(path: str | None = None) -> dict[str, object]:
    container = ServiceContainer()
    snapshot = container.project_service.open_project(Path(path or '.').resolve())
    container.project_service.reparse_all_notebooks()
    return snapshot
