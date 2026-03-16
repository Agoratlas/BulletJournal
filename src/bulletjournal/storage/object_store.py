from __future__ import annotations

import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import StorageKind
from bulletjournal.domain.hashing import sha256_bytes
from bulletjournal.runtime.serializers import deserialize_value, serialize_file, serialize_value
from bulletjournal.storage.atomic_write import atomic_copy_file, atomic_write_bytes
from bulletjournal.storage.project_fs import ProjectPaths


class ObjectStore:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def object_path(self, artifact_hash: str) -> Path:
        prefix = artifact_hash[:2]
        suffix = artifact_hash[2:]
        return self.paths.object_store_dir / prefix / suffix

    def persist_value(self, value: Any, data_type: str) -> dict[str, Any]:
        serialized = serialize_value(value, data_type)
        artifact_hash = sha256_bytes(serialized['bytes'])
        object_path = self.object_path(artifact_hash)
        if not object_path.exists():
            atomic_write_bytes(object_path, serialized['bytes'])
            os.chmod(object_path, 0o444)
        return {
            'artifact_hash': artifact_hash,
            'storage_kind': serialized['storage_kind'],
            'data_type': serialized['data_type'],
            'size_bytes': len(serialized['bytes']),
            'extension': serialized.get('extension'),
            'mime_type': serialized.get('mime_type'),
            'preview': serialized.get('preview'),
        }

    def persist_file(self, file_path: Path, *, data_type: str = 'file', extension: str | None = None) -> dict[str, Any]:
        serialized = serialize_file(file_path, extension=extension)
        artifact_hash = sha256_bytes(serialized['bytes'])
        object_path = self.object_path(artifact_hash)
        if not object_path.exists():
            atomic_copy_file(file_path, object_path)
            os.chmod(object_path, 0o444)
        mime_type, _ = mimetypes.guess_type(f'data{serialized.get("extension") or ""}')
        return {
            'artifact_hash': artifact_hash,
            'storage_kind': StorageKind.FILE.value,
            'data_type': data_type,
            'size_bytes': len(serialized['bytes']),
            'extension': serialized.get('extension'),
            'mime_type': mime_type,
            'preview': serialized.get('preview'),
        }

    def load_value(self, artifact_hash: str, data_type: str) -> Any:
        return deserialize_value(self.object_path(artifact_hash).read_bytes(), data_type)

    def load_file_path(self, artifact_hash: str) -> Path:
        return self.object_path(artifact_hash)

    def create_temp_file(self, suffix: str = '') -> Path:
        self.paths.uploads_temp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=self.paths.uploads_temp_dir, suffix=suffix)
        os.close(fd)
        return Path(temp_path)
