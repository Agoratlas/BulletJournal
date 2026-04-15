from __future__ import annotations

from typing import Any


def stale_input_warning(logical_artifact_id: str, age_text: str | None = None) -> dict[str, Any]:
    message = f'Loaded stale artifact `{logical_artifact_id}`.'
    if age_text:
        message = f'{message} Generated {age_text} ago.'
    return {'code': 'stale_input', 'message': message, 'artifact': logical_artifact_id}


def outdated_input_warning(logical_artifact_id: str) -> dict[str, Any]:
    return {
        'code': 'outdated_input',
        'message': f'Loaded artifact `{logical_artifact_id}` is no longer the current head.',
        'artifact': logical_artifact_id,
    }


def interactive_lineage_warning() -> dict[str, Any]:
    return {
        'code': 'interactive_heuristic_lineage',
        'message': 'Artifact lineage was captured from an interactive Marimo session and is heuristic.',
    }
