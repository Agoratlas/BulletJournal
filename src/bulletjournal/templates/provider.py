from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence


@dataclass(slots=True, frozen=True)
class TemplateAsset:
    provider: str
    kind: str
    name: str
    file_name: str
    ref: str
    path: Path | None
    origin_revision: str
    hidden: bool = False
    title: str | None = None
    description: str | None = None
    source_loader: Callable[[], str] | None = None
    aliases: tuple[str, ...] = ()

    def read_text(self) -> str:
        if self.path is not None:
            return self.path.read_text(encoding='utf-8')
        if self.source_loader is not None:
            return self.source_loader()
        raise FileNotFoundError(f'No template source available for `{self.ref}`.')


class TemplateProvider(Protocol):
    provider_name: str
    provider_revision: str

    def list_notebook_templates(self) -> Sequence[Any]: ...

    def list_pipeline_templates(self) -> Sequence[Any]: ...

    def load_notebook_template(self, name: str) -> str: ...

    def load_pipeline_template(self, name: str) -> str: ...
