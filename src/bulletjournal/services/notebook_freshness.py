from __future__ import annotations

from typing import Any

from bulletjournal.domain.enums import ArtifactState, NodeKind
from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.hashing import combine_hashes
from bulletjournal.domain.models import GraphData
from bulletjournal.parser.source_hash import compute_source_hash


def notebook_uses_execution_head(project_service, node_id: str, interface: dict[str, Any] | None = None) -> bool:
    node = project_service.get_node(node_id)
    if node.kind != NodeKind.NOTEBOOK:
        return False
    resolved_interface = interface if interface is not None else project_service.latest_interface(node_id)
    if resolved_interface is None:
        return True
    if resolved_interface.get('outputs'):
        return False
    return not project_service.require_project().state_db.list_asset_declarations(node_id)


def lineage_metadata_for_notebook(project_service, node_id: str, graph: GraphData) -> dict[str, str] | None:
    interface = project_service.latest_interface(node_id)
    if interface is None:
        return None
    source_hash = interface.get('source_hash')
    if not isinstance(source_hash, str) or not source_hash:
        return None
    input_hashes: list[str] = []
    input_code_hashes: list[str] = []
    state_db = project_service.require_project().state_db
    for port in interface.get('inputs', []):
        binding = resolve_input_binding(graph, node_id=node_id, input_name=str(port['name']))
        if binding is None:
            if bool(port.get('has_default', False)):
                input_hashes.append(_default_hash(project_service, port))
                input_code_hashes.append('default')
                continue
            return None
        head = state_db.get_artifact_head(binding[0], binding[1])
        if head is None or head.get('current_version_id') is None:
            return None
        if head.get('state') != ArtifactState.READY.value:
            return None
        artifact_hash = head.get('artifact_hash')
        upstream_code_hash = head.get('upstream_code_hash')
        if not isinstance(artifact_hash, str) or not artifact_hash:
            return None
        if not isinstance(upstream_code_hash, str) or not upstream_code_hash:
            return None
        input_hashes.append(artifact_hash)
        input_code_hashes.append(upstream_code_hash)
    lineage_key = f'{node_id}/__execution__'
    return {
        'source_hash': source_hash,
        'upstream_data_hash': combine_hashes([source_hash, lineage_key, *input_hashes]),
        'upstream_code_hash': combine_hashes([source_hash, lineage_key, *input_code_hashes]),
    }


def current_source_hash(project_service, node_id: str) -> str | None:
    node = project_service.get_node(node_id)
    if node.kind != NodeKind.NOTEBOOK:
        return None
    path = project_service.require_project().paths.notebook_path(node_id)
    if not path.exists():
        return None
    return compute_source_hash(path)


def _default_hash(project_service, port: dict[str, Any]) -> str:
    from bulletjournal.domain.hashing import hash_json

    return hash_json(port.get('default'))
