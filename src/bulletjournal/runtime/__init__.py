from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any


def __getattr__(name: str) -> Any:
    if name == 'artifacts':
        return import_module('bulletjournal.runtime.artifacts')
    if name == 'assets':
        return import_module('bulletjournal.runtime.assets')
    if name == 'files':
        return import_module('bulletjournal.runtime.files')
    if name in {'get_node_id', 'get_project_id'}:
        return getattr(import_module('bulletjournal.runtime.context'), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


if TYPE_CHECKING:
    import bulletjournal.runtime.artifacts as artifacts
    import bulletjournal.runtime.assets as assets
    import bulletjournal.runtime.files as files
    from bulletjournal.runtime.context import get_node_id, get_project_id

__all__ = ['artifacts', 'assets', 'files', 'get_node_id', 'get_project_id']
