from __future__ import annotations

from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError
from bulletjournal.domain.models import AssetDeclaration, NotebookInterface, ParsedNotebookContract, ValidationIssue
from bulletjournal.parser import parse_notebook_contract
from bulletjournal.parser.validation import build_issue


class NotebookService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def reparse_notebook(self, node_id: str) -> dict[str, Any]:
        project = self.project_service.require_project()
        node = self.project_service.get_node(node_id)
        if node.kind != NodeKind.NOTEBOOK:
            raise InvalidRequestError(f'Node `{node_id}` is not a notebook.')
        notebook_path = project.paths.notebook_path(node_id)
        if not notebook_path.exists():
            raise FileNotFoundError(f'Notebook file not found: {notebook_path}')
        previous_contract = project.state_db.latest_notebook_contract_json(node_id)
        previous_interface = _contract_interface(previous_contract)
        contract = parse_notebook_contract(notebook_path, node_id=node_id)
        interface_payload = contract.interface.to_dict()
        blocking_errors = [issue for issue in contract.issues if issue.severity == ValidationSeverity.ERROR]
        project.state_db.replace_validation_issues(node_id, contract.issues)
        removed_edges: list[dict[str, Any]] = []
        if not blocking_errors:
            removed_edges = self._sync_ports(node_id, previous_interface, contract.interface)
            asset_declarations_changed = self._sync_asset_declarations(node_id, previous_contract, contract)
            durable_warnings = self._removed_edge_warnings(node_id=node_id, removed_edges=removed_edges)
            project.state_db.save_notebook_contract(node_id, contract.source_hash, contract.docs, contract.to_dict())
            if asset_declarations_changed and self.project_service.dashboard_service is not None:
                self.project_service.dashboard_service.refresh_dashboards_for_source(node_id)
            for warning in durable_warnings:
                self.project_service.record_notice(
                    issue_id=warning.issue_id,
                    node_id=warning.node_id,
                    severity=warning.severity,
                    code=warning.code,
                    message=warning.message,
                    details=warning.details,
                )
            changed = previous_interface is not None and previous_interface.get('source_hash') != contract.source_hash
            first_parse = previous_interface is None
            if changed or first_parse:
                self.project_service.record_notebook_activity()
            if changed:
                if self.project_service.run_service is not None:
                    self.project_service.run_service.interrupt_active_run_if_nodes_affected(
                        [node_id],
                        self.project_service.graph(),
                    )
                self._mark_node_outputs_stale(node_id)
                from bulletjournal.services.graph_service import GraphService

                GraphService(self.project_service).mark_downstream_stale([node_id])
        graph = self.project_service.graph()
        self.project_service.event_service.publish(
            'notebook.reparsed',
            project_id=project.metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={
                'node_id': node_id,
                'removed_edges': removed_edges,
                'source_hash': contract.source_hash,
                'applied': not blocking_errors,
            },
        )
        self.project_service.event_service.publish(
            'validation.updated',
            project_id=project.metadata.project_id,
            graph_version=int(graph.meta['graph_version']),
            payload={'node_id': node_id, 'issues': [issue.to_dict() for issue in contract.issues]},
        )
        return interface_payload

    def create_notebook_file(self, node_id: str, source: str) -> Path:
        project = self.project_service.require_project()
        path = project.paths.notebook_path(node_id)
        path.write_text(source, encoding='utf-8')
        return path

    def _sync_ports(
        self,
        node_id: str,
        previous: dict[str, Any] | None,
        current: NotebookInterface,
    ) -> list[dict[str, Any]]:
        project = self.project_service.require_project()
        previous_outputs = _output_ports(previous)
        current_outputs = {port.name: port for port in current.outputs}
        removed_output_names = [
            name
            for name, previous_port in previous_outputs.items()
            if name not in current_outputs or current_outputs[name].data_type != previous_port['data_type']
        ]

        current_input_names = {port.name for port in current.inputs}
        previous_inputs = _input_ports(previous)
        removed_input_names = [
            name
            for name, previous_port in previous_inputs.items()
            if name not in current_input_names or _input_type(current, name) != previous_port['data_type']
        ]

        for port in current.outputs:
            project.state_db.ensure_artifact_head(node_id, port.name, ArtifactState.PENDING)

        for name in removed_output_names:
            project.state_db.delete_artifact_state(node_id, name)

        from bulletjournal.services.graph_service import GraphService

        removed_edges = GraphService(self.project_service).remove_edges_for_port_changes(
            node_id=node_id,
            removed_source_ports=removed_output_names,
            removed_target_ports=removed_input_names,
        )
        return removed_edges

    def _removed_edge_warnings(
        self,
        *,
        node_id: str,
        removed_edges: list[dict[str, Any]],
    ) -> list[ValidationIssue]:
        if not removed_edges:
            return []
        return [
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.WARNING,
                code='edges_removed_for_port_change',
                message=(
                    'Graph edges were removed after notebook port changes: '
                    + '; '.join(
                        f'{edge["source_node"]}/{edge["source_port"]} -> {edge["target_node"]}/{edge["target_port"]}'
                        for edge in removed_edges
                    )
                ),
                details={
                    'removed_edge_ids': [str(edge['id']) for edge in removed_edges],
                    'removed_edge_count': len(removed_edges),
                    'removed_edges': removed_edges,
                },
            )
        ]

    def _mark_node_outputs_stale(self, node_id: str) -> None:
        project = self.project_service.require_project()
        interface_json = project.state_db.latest_interface_json(node_id)
        if interface_json is None:
            return
        for port in interface_json.get('outputs', []):
            project.state_db.ensure_artifact_head(node_id, port['name'], ArtifactState.PENDING)
            head = project.state_db.get_artifact_head(node_id, port['name'])
            if head and head['current_version_id'] is not None:
                project.state_db.set_artifact_head_state(node_id, port['name'], ArtifactState.STALE)
        for head in project.state_db.list_asset_heads(node_id=node_id):
            if head.get('current_asset_version_id') is None:
                continue
            project.state_db.set_asset_head_state(node_id, str(head['asset_name']), ArtifactState.STALE)

    def _sync_asset_declarations(
        self,
        node_id: str,
        previous_contract: dict[str, Any] | None,
        current: ParsedNotebookContract,
    ) -> bool:
        project = self.project_service.require_project()
        previous_signatures = [
            _asset_declaration_signature(declaration)
            for declaration in (previous_contract or {}).get('asset_declarations', [])
            if isinstance(declaration, dict)
        ]
        current_signatures = [_asset_declaration_signature(declaration) for declaration in current.asset_declarations]
        previous_names = {
            str(declaration['name'])
            for declaration in (previous_contract or {}).get('asset_declarations', [])
            if isinstance(declaration, dict) and declaration.get('name')
        }
        current_names = {declaration.name for declaration in current.asset_declarations}
        project.state_db.replace_asset_declarations(node_id, current.source_hash, current.asset_declarations)
        for declaration in current.asset_declarations:
            project.state_db.ensure_asset_head(node_id, declaration.name, ArtifactState.PENDING)
        for removed_name in sorted(previous_names - current_names):
            project.state_db.delete_asset_state(node_id, removed_name)
        return previous_signatures != current_signatures


def _output_ports(interface_json: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if interface_json is None:
        return {}
    return {port['name']: port for port in interface_json.get('outputs', [])}


def _input_ports(interface_json: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if interface_json is None:
        return {}
    return {port['name']: port for port in interface_json.get('inputs', [])}


def _input_type(interface: NotebookInterface, name: str) -> str | None:
    for port in interface.inputs:
        if port.name == name:
            return port.data_type
    return None


def _contract_interface(contract_json: dict[str, Any] | None) -> dict[str, Any] | None:
    if contract_json is None:
        return None
    interface = contract_json.get('interface')
    if isinstance(interface, dict):
        return interface
    return contract_json


def _asset_declaration_signature(
    declaration: dict[str, Any] | AssetDeclaration,
) -> tuple[str, str | None, str | None, str | None, int | None]:
    if isinstance(declaration, AssetDeclaration):
        return (
            declaration.name,
            declaration.title,
            declaration.description,
            declaration.declared_asset_type,
            declaration.declaration_index,
        )
    return (
        str(declaration.get('name') or '').strip(),
        None if declaration.get('title') is None else str(declaration.get('title')),
        None if declaration.get('description') is None else str(declaration.get('description')),
        None if declaration.get('declared_asset_type') is None else str(declaration.get('declared_asset_type')),
        int(declaration['declaration_index']) if declaration.get('declaration_index') is not None else None,
    )
