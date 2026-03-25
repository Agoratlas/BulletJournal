from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any


def __getattr__(name: str) -> Any:
    if name == 'artifacts':
        return import_module('bulletjournal.runtime.artifacts')
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


if TYPE_CHECKING:
    import bulletjournal.runtime.artifacts as artifacts

__all__ = ['artifacts']
