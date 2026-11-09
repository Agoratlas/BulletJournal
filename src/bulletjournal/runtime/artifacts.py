from __future__ import annotations

from pathlib import Path
from typing import Any, overload

from bulletjournal.domain.enums import ArtifactRole
from bulletjournal.runtime.context import current_runtime_context
from bulletjournal.runtime.file_artifacts import FilePushHandle
from bulletjournal.runtime.files import FileSnapshot, capture
from bulletjournal.runtime.serializers import validate_runtime_value_type


def pull(*, name: str, data_type: Any, default: Any = None, description: str | None = None) -> Any:
    """Load and return the value connected to a named notebook input, validating it against the declared data type.
    If the input is unconnected, its declared default is used; without a default, the run fails.

    - ``name``: Artifact identifier, written in snake_case.
    - ``data_type``: Expected Python type, such as ``int``, ``str``, or ``pd.DataFrame``.
    - ``default``: Fallback for an unconnected input; must be a literal, JSON-serializable value.
      Omit it to require a connection.
    - ``description``: Documentation string for this input.
    """
    del description
    context = current_runtime_context()
    if default is not None:
        validate_runtime_value_type(default, data_type, operation='default')
    context.validate_pull_contract(name=name, data_type=_normalize_runtime_type(data_type))
    metadata = context.resolve_pull(name)
    if metadata['value'] is not None:
        validate_runtime_value_type(metadata['value'], data_type, operation='import')
    context.record_pull(name, metadata)
    return metadata['value']


def pull_file(*, name: str, allow_missing: bool = False, description: str | None = None):
    """Return the local file path for the artifact connected to a named notebook input.
    If the input is unconnected, the run fails unless ``allow_missing=True``, in which case it returns ``None``.

    - ``name``: Artifact identifier, written in snake_case.
    - ``allow_missing``: Allow an unconnected input and return ``None``; defaults to ``False``.
    - ``description``: Documentation string for this input.
    """
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
    optional: bool = False,
    description: str | None = None,
    **kwargs: Any,
) -> None:
    """Publish a Python value as a named output artifact so downstream notebooks can consume it.

    - ``value``: The Python value to publish.
    - ``name``: Artifact identifier, written in snake_case.
    - ``data_type``: Expected Python type, such as ``int``, ``str``, or ``pd.DataFrame``.
    - ``optional``: Set to ``True`` to allow publishing ``None``; defaults to ``False``.
    - ``description``: Documentation string for this output.
    """
    del description
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected artifact push kwargs: {unexpected}')
    if value is None and not optional:
        raise TypeError('artifact.push(..., value=None) requires optional=True.')
    if value is not None:
        validate_runtime_value_type(value, data_type, operation='export')
    context = current_runtime_context()
    normalized = _normalize_runtime_type(data_type)
    context.finalize_value_push(
        name=name,
        value=value,
        data_type=normalized,
        role=ArtifactRole.OUTPUT,
    )


@overload
def push_file(
    source: str | Path | FileSnapshot,
    *,
    name: str,
    extension: str | None = None,
    description: str | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def push_file(
    *,
    name: str,
    extension: str | None = None,
    description: str | None = None,
    **kwargs: Any,
) -> FilePushHandle: ...


def push_file(
    source: str | Path | FileSnapshot | None = None,
    *,
    name: str,
    extension: str | None = None,
    description: str | None = None,
    **kwargs: Any,
) -> FilePushHandle | None:
    """Publish an existing path or FileSnapshot as a file artifact.
    Without a source, return a context manager for writing a file and publishing it
    when the ``with`` block completes successfully.

    - ``source``: Optional existing file path or captured file; its suffix is preserved.
    - ``name``: Artifact identifier, written in snake_case.
    - ``extension``: Optional literal filename suffix, such as ``.csv`` or ``.png``.
    - ``description``: Documentation string for this output.
    """
    del description
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected artifact push_file kwargs: {unexpected}')
    if source is not None:
        snapshot = capture(source, extension=extension)
        current_runtime_context().finalize_file_push(
            name=name,
            temp_path=snapshot.path,
            role=ArtifactRole.OUTPUT,
            extension=snapshot.extension,
        )
        return None
    return FilePushHandle(name=name, role=ArtifactRole.OUTPUT, extension=extension)


def _normalize_runtime_type(data_type: Any) -> str:
    if isinstance(data_type, str):
        return data_type if data_type in {'int', 'float', 'bool', 'str', 'list', 'dict', 'object'} else 'object'
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
