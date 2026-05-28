from __future__ import annotations

import re

ARTIFACT_NAME_PATTERN = re.compile(r'^[a-z0-9_]+$')


def is_valid_artifact_name(value: str) -> bool:
    return bool(ARTIFACT_NAME_PATTERN.fullmatch(value))


def invalid_artifact_name_message(name: str) -> str:
    return f'Invalid artifact name `{name}`, must only contain lowercase letters, digits and underscores.'
