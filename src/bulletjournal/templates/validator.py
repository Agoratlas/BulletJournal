from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import ArtifactRole, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.domain.graph_rules import validate_acyclic, validate_unique_edge_ids, validate_unique_node_ids, validate_unique_target_ports
from bulletjournal.domain.models import Edge, Node, Port
from bulletjournal.domain.type_system import types_compatible
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.parser.validation import build_issue
from bulletjournal.templates.registry import BUILTIN_PROVIDER, builtin_templates


BUILTIN_NOTEBOOK_TEMPLATE_ROOT = Path(__file__).resolve().parent / 'builtin'


def validate_template(path: Path, *, notebook_paths_by_ref: dict[str, Path] | None = None) -> list[dict[str, object]]:
    if path.suffix == '.py':
        interface = parse_notebook_interface(path, node_id=path.stem)
        return [issue.to_dict() for issue in interface.issues]
    if path.suffix == '.json':
        return validate_pipeline_template(path, notebook_paths_by_ref=notebook_paths_by_ref)
    return [
        build_issue(
            node_id=path.stem,
            severity=ValidationSeverity.ERROR,
            code='unsupported_template_type',
            message=f'Unsupported template file `{path.name}`.',
        ).to_dict()
    ]


def validate_pipeline_template(path: Path, *, notebook_paths_by_ref: dict[str, Path] | None = None) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    try:
        definition = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return [
            build_issue(
                node_id=path.stem,
                severity=ValidationSeverity.ERROR,
                code='invalid_pipeline_template_json',
                message=f'Invalid JSON in pipeline template: {exc.msg}.',
                details={'line': exc.lineno, 'column': exc.colno},
            ).to_dict()
        ]

    if not isinstance(definition, dict):
        return [
            build_issue(
                node_id=path.stem,
                severity=ValidationSeverity.ERROR,
                code='invalid_pipeline_template_shape',
                message='Pipeline template root must be a JSON object.',
            ).to_dict()
        ]

    nodes_raw = definition.get('nodes')
    edges_raw = definition.get('edges')
    layout_raw = definition.get('layout')
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list) or not isinstance(layout_raw, list):
        return [
            build_issue(
                node_id=path.stem,
                severity=ValidationSeverity.ERROR,
                code='invalid_pipeline_template_shape',
                message='Pipeline templates must define list fields `nodes`, `edges`, and `layout`.',
            ).to_dict()
        ]

    resolved_notebooks = notebook_paths_by_ref or {
        f'{BUILTIN_PROVIDER}/{template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).with_suffix("" ).as_posix()}': template_path
        for template_path in builtin_templates()
    }
    if notebook_paths_by_ref is None:
        for template_path in builtin_templates():
            canonical_ref = template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).with_suffix('').as_posix()
            legacy_ref = template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).as_posix()
            resolved_notebooks[f'{BUILTIN_PROVIDER}/{canonical_ref}'] = template_path
            resolved_notebooks[legacy_ref] = template_path
            resolved_notebooks[canonical_ref] = template_path

    graph_nodes: list[Node] = []
    graph_edges: list[Edge] = []
    node_rows: dict[str, dict[str, Any]] = {}
    layout_rows: dict[str, dict[str, Any]] = {}
    interfaces_by_node: dict[str, dict[str, Any]] = {}

    for index, raw_node in enumerate(nodes_raw):
        if not isinstance(raw_node, dict):
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_node',
                    message=f'Node entry #{index + 1} must be an object.',
                ).to_dict()
            )
            continue
        node_id = str(raw_node.get('id') or '').strip()
        title = str(raw_node.get('title') or '').strip()
        kind_value = str(raw_node.get('kind') or '').strip()
        if not node_id or not title or kind_value not in {NodeKind.NOTEBOOK.value, NodeKind.FILE_INPUT.value}:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_node',
                    message=f'Node `{node_id or index + 1}` must define `id`, `title`, and a supported `kind`.',
                ).to_dict()
            )
            continue
        node_rows[node_id] = raw_node
        graph_nodes.append(Node(id=node_id, kind=NodeKind(kind_value), title=title))
        try:
            interfaces_by_node[node_id] = _pipeline_node_interface(raw_node, notebook_paths_by_ref=resolved_notebooks)
        except FileNotFoundError:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='missing_template_ref',
                    message=f'Node `{node_id}` references unknown notebook template `{raw_node.get("template_ref")}`.',
                ).to_dict()
            )

    for index, raw_layout in enumerate(layout_raw):
        if not isinstance(raw_layout, dict):
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_layout',
                    message=f'Layout entry #{index + 1} must be an object.',
                ).to_dict()
            )
            continue
        node_id = str(raw_layout.get('node_id') or '').strip()
        if not node_id:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_layout',
                    message='Each layout entry must define `node_id`.',
                ).to_dict()
            )
            continue
        layout_rows[node_id] = raw_layout

    for node_id in node_rows:
        if node_id not in layout_rows:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='missing_pipeline_layout',
                    message=f'Node `{node_id}` is missing a layout entry.',
                ).to_dict()
            )
    for node_id in layout_rows:
        if node_id not in node_rows:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='unknown_layout_node',
                    message=f'Layout entry references unknown node `{node_id}`.',
                ).to_dict()
            )

    for raw_edge in edges_raw:
        if not isinstance(raw_edge, dict):
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_edge',
                    message='Edge entries must be objects.',
                ).to_dict()
            )
            continue
        source_node = str(raw_edge.get('source_node') or '').strip()
        source_port = str(raw_edge.get('source_port') or '').strip()
        target_node = str(raw_edge.get('target_node') or '').strip()
        target_port = str(raw_edge.get('target_port') or '').strip()
        if not source_node or not source_port or not target_node or not target_port:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_pipeline_edge',
                    message='Each edge must define source and target nodes and ports.',
                ).to_dict()
            )
            continue
        graph_edges.append(
            Edge(
                id=f'{source_node}.{source_port}__{target_node}.{target_port}',
                source_node=source_node,
                source_port=source_port,
                target_node=target_node,
                target_port=target_port,
            )
        )
        source_interface = interfaces_by_node.get(source_node)
        target_interface = interfaces_by_node.get(target_node)
        if source_interface is None or target_interface is None:
            continue
        source_type = _port_data_type(source_interface.get('outputs', []) + source_interface.get('assets', []), source_port)
        target_type = _port_data_type(target_interface.get('inputs', []), target_port)
        if source_type is None:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='unknown_source_port',
                    message=f'Edge references unknown source port `{source_node}.{source_port}`.',
                ).to_dict()
            )
            continue
        if target_type is None:
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='unknown_target_port',
                    message=f'Edge references unknown target port `{target_node}.{target_port}`.',
                ).to_dict()
            )
            continue
        if not types_compatible(source_type, target_type):
            issues.append(
                build_issue(
                    node_id=path.stem,
                    severity=ValidationSeverity.ERROR,
                    code='incompatible_edge_types',
                    message=f'Cannot connect `{source_node}.{source_port}` ({source_type}) to `{target_node}.{target_port}` ({target_type}).',
                ).to_dict()
            )

    if issues:
        return sorted(issues, key=lambda item: (str(item.get('severity')), str(item.get('code')), str(item.get('message'))))

    try:
        validate_unique_node_ids(graph_nodes)
        validate_unique_edge_ids(graph_edges)
        validate_unique_target_ports(graph_edges)
        validate_acyclic(graph_nodes, graph_edges)
    except GraphValidationError as exc:
        issues.append(
            build_issue(
                node_id=path.stem,
                severity=ValidationSeverity.ERROR,
                code='invalid_pipeline_graph',
                message=str(exc),
            ).to_dict()
        )
    return issues


def load_pipeline_template_definition(path: Path) -> dict[str, Any]:
    definition = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(definition, dict):
        raise ValueError('Pipeline template root must be a JSON object.')
    return definition


def _pipeline_node_interface(raw_node: dict[str, Any], *, notebook_paths_by_ref: dict[str, Path]) -> dict[str, Any]:
    kind_value = str(raw_node.get('kind'))
    if kind_value == NodeKind.FILE_INPUT.value:
        artifact_name = _pipeline_file_input_name(raw_node)
        output = Port(
            name=artifact_name,
            data_type='file',
            role=ArtifactRole.OUTPUT,
            description='Uploaded file',
            kind='file',
            direction='output',
        )
        return {'inputs': [], 'outputs': [output.to_dict()], 'assets': []}
    template_ref = str(raw_node.get('template_ref') or '')
    template_path = notebook_paths_by_ref.get(template_ref)
    if template_path is None:
        raise FileNotFoundError(template_ref)
    return parse_notebook_interface(template_path, node_id=str(raw_node.get('id') or template_path.stem)).to_dict()


def _pipeline_file_input_name(raw_node: dict[str, Any]) -> str:
    artifact_name = raw_node.get('artifact_name')
    if isinstance(artifact_name, str) and artifact_name.strip():
        return artifact_name.strip()
    ui = raw_node.get('ui')
    if isinstance(ui, dict):
        candidate = ui.get('artifact_name')
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return 'file'


def _port_data_type(ports: list[dict[str, Any]], name: str) -> str | None:
    for port in ports:
        if port.get('name') == name:
            return str(port.get('data_type'))
    return None
