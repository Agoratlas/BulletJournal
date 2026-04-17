from __future__ import annotations

from typing import Any

from bulletjournal.domain.enums import ArtifactRole
from bulletjournal.runtime.context import current_runtime_context
from bulletjournal.runtime.file_artifacts import FilePushHandle


def pull(*, name: str, data_type: Any, default: Any = None, description: str | None = None) -> Any:
    del description
    del default
    context = current_runtime_context()
    context.validate_pull_contract(name=name, data_type=_normalize_runtime_type(data_type))
    metadata = context.resolve_pull(name)
    context.record_pull(name, metadata)
    return metadata['value']


def pull_file(*, name: str, allow_missing: bool = False, description: str | None = None):
    del description
    context = current_runtime_context()
    metadata = context.resolve_pull_file(name=name, allow_missing=allow_missing)
    context.record_pull(name, metadata)
    path = metadata['path']
    return None if path is None else str(path)


def push(
    value: Any,
    *,
    name: str,
    data_type: Any,
    description: str | None = None,
    **kwargs: Any,
) -> None:
    del description
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected artifact push kwargs: {unexpected}')
    context = current_runtime_context()
    normalized = _normalize_runtime_type(data_type)
    context.finalize_value_push(
        name=name,
        value=value,
        data_type=normalized,
        role=ArtifactRole.OUTPUT,
    )


def push_file(
    *,
    name: str,
    extension: str | None = None,
    description: str | None = None,
    **kwargs: Any,
) -> FilePushHandle:
    del description
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected artifact push_file kwargs: {unexpected}')
    return FilePushHandle(name=name, role=ArtifactRole.OUTPUT, extension=extension)


def _normalize_runtime_type(data_type: Any) -> str:
    if isinstance(data_type, str):
        return data_type
    module = getattr(data_type, '__module__', '')
    name = getattr(data_type, '__name__', None)
    if data_type in {int, float, bool, str, list, dict, object}:
        return data_type.__name__
    if module == 'builtins' and name in {'int', 'float', 'bool', 'str', 'list', 'dict', 'object'}:
        return str(name)
    if module in {'pandas.core.frame', 'pandas'} and name == 'DataFrame':
        return 'pandas.DataFrame'
    if module in {'pandas.core.series', 'pandas'} and name == 'Series':
        return 'pandas.Series'
    if module.startswith('networkx') and name in {'Graph', 'DiGraph'}:
        return f'networkx.{name}'
    return 'object'
