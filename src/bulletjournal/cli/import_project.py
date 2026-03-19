from __future__ import annotations

from pathlib import Path

from bulletjournal.storage.project_archive import import_project_archive


def import_project(archive_path: str, path: str) -> dict[str, object]:
    return import_project_archive(Path(archive_path), Path(path))
