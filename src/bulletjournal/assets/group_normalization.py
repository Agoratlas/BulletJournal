from __future__ import annotations


def normalize_group_normalization(value: object) -> str | None:
    if value is True:
        return 'sum'
    if value is False:
        return 'none'
    if isinstance(value, str) and value in {'none', 'max', 'sum'}:
        return value
    return None
