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
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.storage.state_db import StateDB


class ObjectStore:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.db = StateDB(paths.state_db_path)
        self.lock = ProjectLock(paths.project_lock_path)

    def object_path(self, artifact_hash: str) -> Path:
        prefix = artifact_hash[:2]
        suffix = artifact_hash[2:]
        return self.paths.object_store_dir / prefix / suffix

    def quarantine_path(self, artifact_hash: str) -> Path:
        return self.paths.object_quarantine_dir / artifact_hash[:2] / artifact_hash[2:]

    def persist_value(self, value: Any, data_type: str) -> dict[str, Any]:
        serialized = serialize_value(value, data_type)
        artifact_hash = sha256_bytes(serialized['bytes'])
        with self.lock.exclusive():
            self._prepare_path(artifact_hash, serialized['bytes'])
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
        with self.lock.exclusive():
            object_path = self.object_path(artifact_hash)
            if not self.restore_quarantined(artifact_hash):
                if object_path.exists():
                    self._verify_path(object_path, artifact_hash)
                else:
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
        with self.lock.shared():
            path = self.object_path(artifact_hash)
            self._verify_path(path, artifact_hash)
            return deserialize_value(path.read_bytes(), data_type)

    def load_file_path(self, artifact_hash: str, *, extension: str | None = None) -> Path:
        object_path = self.object_path(artifact_hash)
        self._verify_path(object_path, artifact_hash)
        normalized_extension = self._normalize_extension(extension)
        if not normalized_extension:
            return object_path
        self.paths.pulled_files_dir.mkdir(parents=True, exist_ok=True)
        materialized_path = self.paths.pulled_files_dir / f'{artifact_hash}{normalized_extension}'
        if materialized_path.exists() or materialized_path.is_symlink():
            return materialized_path
        try:
            os.symlink(object_path, materialized_path)
        except FileExistsError:
            return materialized_path
        except OSError:
            atomic_copy_file(object_path, materialized_path)
            os.chmod(materialized_path, 0o444)
        return materialized_path

    def verify_object(self, artifact_hash: str, expected_size: int) -> None:
        path = self.object_path(artifact_hash)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.stat().st_size != expected_size:
            raise ValueError(f'Object size check failed for {artifact_hash}.')
        self._verify_path(path, artifact_hash)

    def create_temp_file(self, suffix: str = '') -> Path:
        self.paths.uploads_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=self.paths.uploads_dir, suffix=suffix)
        os.close(fd)
        return Path(temp_path)

    def restore_quarantined(self, artifact_hash: str) -> bool:
        quarantine = self.quarantine_path(artifact_hash)
        if not quarantine.exists():
            return False
        self._verify_path(quarantine, artifact_hash)
        destination = self.object_path(artifact_hash)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantine, destination)
        with self.db._connection() as connection:
            connection.execute(
                "UPDATE objects SET gc_state = 'active', gc_marked_at = NULL, quarantined_at = NULL, "
                'delete_after = NULL, quarantine_path = NULL, unreferenced_at = NULL WHERE artifact_hash = ?',
                (artifact_hash,),
            )
            connection.commit()
        return True

    def _prepare_path(self, artifact_hash: str, content: bytes) -> None:
        object_path = self.object_path(artifact_hash)
        if self.restore_quarantined(artifact_hash):
            return
        if object_path.exists():
            self._verify_path(object_path, artifact_hash)
            record = self.db.get_object_record(artifact_hash)
            if record is not None and record['gc_state'] != 'active':
                with self.db._connection() as connection:
                    connection.execute(
                        "UPDATE objects SET gc_state = 'active', gc_marked_at = NULL, quarantined_at = NULL, "
                        'delete_after = NULL, quarantine_path = NULL, unreferenced_at = NULL WHERE artifact_hash = ?',
                        (artifact_hash,),
                    )
                    connection.commit()
            return
        atomic_write_bytes(object_path, content)
        os.chmod(object_path, 0o444)

    @staticmethod
    def _verify_path(path: Path, artifact_hash: str) -> None:
        if sha256_bytes(path.read_bytes()) != artifact_hash:
            raise ValueError(f'Object integrity check failed for {artifact_hash}.')

    @staticmethod
    def _normalize_extension(extension: str | None) -> str:
        if not extension:
            return ''
        return extension if extension.startswith('.') else f'.{extension}'
