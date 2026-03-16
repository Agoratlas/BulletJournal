from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def json_dumps(value: Any, *, pretty: bool = False) -> str:
    kwargs = {'sort_keys': True, 'ensure_ascii': True}
    if pretty:
        kwargs.update({'indent': 2})
    else:
        kwargs.update({'separators': (',', ':')})
    return json.dumps(value, **kwargs)


def slugify(value: str) -> str:
    candidate = re.sub(r'[^a-zA-Z0-9]+', '_', value.strip().lower()).strip('_')
    return candidate or 'bulletjournal_project'


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def python_version_string() -> str:
    return '.'.join(str(part) for part in sys.version_info[:3])
