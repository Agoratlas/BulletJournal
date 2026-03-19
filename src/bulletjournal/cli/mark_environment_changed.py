from __future__ import annotations

from pathlib import Path

from bulletjournal.api.deps import ServiceContainer


def mark_environment_changed(path: str, *, reason: str) -> dict[str, object]:
    container = ServiceContainer()
    container.project_service.open_project(Path(path).resolve())
    return container.project_service.mark_environment_changed(reason=reason, mark_all_artifacts_stale=True)
