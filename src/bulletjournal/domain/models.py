from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bulletjournal.domain.enums import ArtifactRole, NodeKind, ValidationSeverity


@dataclass(slots=True)
class TemplateRef:
    kind: str
    ref: str
    origin_revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'ref': self.ref,
            'origin_revision': self.origin_revision,
        }


@dataclass(slots=True)
class Node:
    id: str
    kind: NodeKind
    title: str
    path: str | None = None
    template: TemplateRef | None = None
    ui: dict[str, Any] = field(default_factory=lambda: {'hidden_inputs': []})

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'kind': self.kind.value,
            'title': self.title,
            'path': self.path,
            'template': self.template.to_dict() if self.template else None,
            'ui': self.ui,
        }


@dataclass(slots=True)
class Edge:
    id: str
    source_node: str
    source_port: str
    target_node: str
    target_port: str

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'source_node': self.source_node,
            'source_port': self.source_port,
            'target_node': self.target_node,
            'target_port': self.target_port,
        }


@dataclass(slots=True)
class LayoutEntry:
    node_id: str
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict[str, Any]:
        return {
            'node_id': self.node_id,
            'x': self.x,
            'y': self.y,
            'w': self.w,
            'h': self.h,
        }


@dataclass(slots=True)
class Port:
    name: str
    data_type: str
    role: ArtifactRole | None = None
    description: str | None = None
    default: Any = None
    has_default: bool = False
    kind: str = 'value'
    direction: str = 'output'

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'data_type': self.data_type,
            'role': None if self.role is None else self.role.value,
            'description': self.description,
            'default': self.default,
            'has_default': self.has_default,
            'kind': self.kind,
            'direction': self.direction,
        }


@dataclass(slots=True)
class ValidationIssue:
    issue_id: str
    node_id: str
    severity: ValidationSeverity
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'issue_id': self.issue_id,
            'node_id': self.node_id,
            'severity': self.severity.value,
            'code': self.code,
            'message': self.message,
            'details': self.details,
        }


@dataclass(slots=True)
class NotebookInterface:
    node_id: str
    source_hash: str
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    assets: list[Port] = field(default_factory=list)
    docs: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'node_id': self.node_id,
            'source_hash': self.source_hash,
            'inputs': [port.to_dict() for port in self.inputs],
            'outputs': [port.to_dict() for port in self.outputs],
            'assets': [port.to_dict() for port in self.assets],
            'docs': self.docs,
            'issues': [issue.to_dict() for issue in self.issues],
        }


@dataclass(slots=True)
class GraphData:
    meta: dict[str, Any]
    nodes: list[Node]
    edges: list[Edge]
    layout: list[LayoutEntry]


@dataclass(slots=True)
class ProjectMetadata:
    project_id: str
    title: str
    created_at: str
    artifact_cache_limit_bytes: int
    tracked_env_vars: list[str]
    default_open_browser: bool


@dataclass(slots=True)
class CheckpointRecord:
    checkpoint_id: str
    created_at: str
    graph_version: int
    path: str
    restored_at: str | None = None


def file_input_artifact_name(node: Node) -> str:
    if node.kind != NodeKind.FILE_INPUT:
        return 'file'
    value = node.ui.get('artifact_name')
    return str(value) if isinstance(value, str) and value else 'file'
