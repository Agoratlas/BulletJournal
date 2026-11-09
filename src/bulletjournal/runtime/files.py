from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """An owned, read-only copy of a file. Create snapshots with ``files.capture(...)``.

    The temporary copy is kept alive by this object and removed when its last owner
    disappears (or the Python process exits). It is outside project temporary-file GC.
    """

    path: Path
    extension: str | None
    size_bytes: int
    _directory: tempfile.TemporaryDirectory = field(repr=False, compare=False)


def capture(source: str | Path | FileSnapshot, *, extension: str | None = None) -> FileSnapshot:
    """Capture a file's current contents without publishing an artifact or asset.

    The source may be deleted or changed afterwards. An existing snapshot is reused.
    ``extension`` overrides the source suffix, e.g. ``'.png'`` or ``'.tar.gz'``.
    """
    return _capture(source, extension=extension)


def _capture(
    source: str | Path | FileSnapshot,
    *,
    extension: str | None = None,
    max_size_bytes: int | None = None,
) -> FileSnapshot:
    if isinstance(source, FileSnapshot):
        _check_size(source.size_bytes, max_size_bytes)
        if extension is None or _normalize_extension(extension) == source.extension:
            return source
        source_path = source.path
    elif isinstance(source, str | Path):
        source_path = Path(source)
    else:
        raise TypeError('File capture requires a string path, pathlib.Path, or FileSnapshot.')
    suffix = _normalize_extension(extension) if extension is not None else source_path.suffix or None
    # TemporaryDirectory owns cleanup, including failed captures and interpreter exit.
    directory = tempfile.TemporaryDirectory(prefix='bulletjournal-capture-')
    destination = Path(directory.name) / f'file{suffix or ""}'
    try:
        with source_path.open('rb') as reader:
            _check_size(os.fstat(reader.fileno()).st_size, max_size_bytes)
            size_bytes = 0
            with destination.open('wb') as writer:
                while chunk := reader.read(1024 * 1024):
                    size_bytes += len(chunk)
                    _check_size(size_bytes, max_size_bytes)
                    writer.write(chunk)
        destination.chmod(0o444)
        return FileSnapshot(destination, suffix, size_bytes, directory)
    except BaseException:
        directory.cleanup()
        raise


def _normalize_extension(extension: str) -> str | None:
    if not isinstance(extension, str):
        raise TypeError('File extension must be a string.')
    if not extension:
        return None
    normalized = extension if extension.startswith('.') else f'.{extension}'
    if not re.fullmatch(r'(?:\.[a-zA-Z0-9_-]+)+', normalized):
        raise ValueError('File extension must be a suffix such as .png or .tar.gz.')
    return normalized


def _check_size(size_bytes: int, max_size_bytes: int | None) -> None:
    if max_size_bytes is not None and size_bytes > max_size_bytes:
        raise ValueError(
            f'Image is {size_bytes:,} bytes, exceeding max_size_bytes={max_size_bytes:,}. '
            'Pass a larger max_size_bytes to assets.Image(...) to allow it.'
        )


__all__ = ['FileSnapshot', 'capture']
