from __future__ import annotations

from pathlib import Path

from bulletjournal.storage.project_archive import ProjectExportMode, export_project_archive


def export_project(
    path: str,
    archive_path: str,
    *,
    mode: ProjectExportMode = ProjectExportMode.FULL,
) -> dict[str, object]:
    return export_project_archive(Path(path), Path(archive_path), mode=mode)
