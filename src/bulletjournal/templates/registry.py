from __future__ import annotations

from pathlib import Path


def builtin_templates() -> list[Path]:
    builtin_dir = Path(__file__).resolve().parent / 'builtin'
    return sorted(builtin_dir.glob('*.py'))
