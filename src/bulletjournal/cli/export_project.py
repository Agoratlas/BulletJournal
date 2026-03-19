from __future__ import annotations

from pathlib import Path

from bulletjournal.storage.project_archive import export_project_archive


def export_project(path: str, archive_path: str, *, include_artifacts: bool = True) -> dict[str, object]:
    return export_project_archive(Path(path), Path(archive_path), include_artifacts=include_artifacts)
