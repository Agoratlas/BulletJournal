from __future__ import annotations

from pathlib import Path


def builtin_templates() -> list[Path]:
    builtin_dir = Path(__file__).resolve().parent / 'builtin'
    return sorted(path for path in builtin_dir.rglob('*.py') if '__pycache__' not in path.parts)


def builtin_pipeline_templates() -> list[Path]:
    pipeline_dir = Path(__file__).resolve().parent / 'pipelines'
    return sorted(path for path in pipeline_dir.rglob('*.json') if '__pycache__' not in path.parts)
