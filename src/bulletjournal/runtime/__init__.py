from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any


def __getattr__(name: str) -> Any:
    if name == 'artifacts':
        return import_module('bulletjournal.runtime.artifacts')
    if name in {'get_node_id', 'get_project_id'}:
        return getattr(import_module('bulletjournal.runtime.context'), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


if TYPE_CHECKING:
    import bulletjournal.runtime.artifacts as artifacts
    from bulletjournal.runtime.context import get_node_id, get_project_id

__all__ = ['artifacts', 'get_node_id', 'get_project_id']
