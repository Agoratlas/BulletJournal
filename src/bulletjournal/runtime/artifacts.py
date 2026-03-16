from __future__ import annotations

from typing import Any

from bulletjournal.domain.enums import ArtifactRole
from bulletjournal.runtime.context import current_runtime_context
from bulletjournal.runtime.file_artifacts import FilePushHandle


class _ArtifactsAPI:
    def pull(self, *, name: str, data_type: Any, default: Any = None, description: str | None = None) -> Any:
        del description
        del default
        context = current_runtime_context()
        context.validate_pull_contract(name=name, data_type=_normalize_runtime_type(data_type))
        metadata = context.resolve_pull(name)
        context.record_pull(name, metadata)
        return metadata['value']

    def pull_file(self, *, name: str, description: str | None = None):
        del description
        context = current_runtime_context()
        metadata = context.resolve_pull_file(name)
        context.record_pull(name, metadata)
        return str(metadata['path'])

    def push(
        self,
        value: Any,
        *,
        name: str,
        data_type: Any,
        is_output: bool = False,
        description: str | None = None,
    ) -> None:
        del description
        context = current_runtime_context()
        role = ArtifactRole.OUTPUT if is_output else ArtifactRole.ASSET
        normalized = _normalize_runtime_type(data_type)
        context.finalize_value_push(name=name, value=value, data_type=normalized, role=role)

    def push_file(
        self,
        *,
        name: str,
        extension: str | None = None,
        is_output: bool = False,
        description: str | None = None,
    ) -> FilePushHandle:
        del description
        role = ArtifactRole.OUTPUT if is_output else ArtifactRole.ASSET
        return FilePushHandle(name=name, role=role, extension=extension)


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


artifacts = _ArtifactsAPI()
