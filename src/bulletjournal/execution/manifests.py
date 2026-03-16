from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RunManifest:
    project_root: str
    node_id: str
    notebook_path: str
    run_id: str
    source_hash: str
    lineage_mode: str
    bindings: dict[str, dict[str, Any]]
    outputs: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RunManifest':
        return cls(**data)

    @property
    def notebook_file(self) -> Path:
        return Path(self.notebook_path)
