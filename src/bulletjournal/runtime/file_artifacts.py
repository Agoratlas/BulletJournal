from __future__ import annotations

from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole
from bulletjournal.runtime.context import current_runtime_context


class FilePushHandle:
    def __init__(self, *, name: str, role: ArtifactRole, extension: str | None):
        self.name = name
        self.role = role
        self.extension = extension or ''
        self._path: Path | None = None

    def __enter__(self) -> Path:
        context = current_runtime_context()
        self._path = context.object_store.create_temp_file(self.extension)
        return self._path

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._path is None:
            return False
        try:
            if exc_type is None:
                context = current_runtime_context()
                context.finalize_file_push(name=self.name, temp_path=self._path, role=self.role)
        finally:
            if self._path.exists():
                self._path.unlink()
        return False
