from __future__ import annotations

import importlib.util
from pathlib import Path

from bulletjournal.storage import is_project_root


def doctor(path: str | None = None) -> dict[str, object]:
    target = Path(path or '.').resolve()
    checks = {
        'project_root': is_project_root(target),
        'fastapi': importlib.util.find_spec('fastapi') is not None,
        'marimo': importlib.util.find_spec('marimo') is not None,
        'pandas': importlib.util.find_spec('pandas') is not None,
        'pyarrow': importlib.util.find_spec('pyarrow') is not None,
    }
    checks['ok'] = all(bool(value) for value in checks.values())
    checks['path'] = str(target)
    return checks
