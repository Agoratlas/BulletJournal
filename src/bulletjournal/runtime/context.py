from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bulletjournal.assets.runtime_types import BaseAsset, asset_type_id_for_class, asset_type_id_for_instance
from bulletjournal.assets.serializer import serialize_asset
from bulletjournal.config import EDIT_STABILIZATION_SECONDS
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, ValidationSeverity
from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.hashing import combine_hashes, hash_json
from bulletjournal.domain.models import AssetDeclaration, GraphData, Port
from bulletjournal.parser.interface_parser import parse_notebook_contract
from bulletjournal.parser.source_hash import compute_source_hash
from bulletjournal.runtime.warnings import (
    interactive_lineage_warning,
    outdated_input_warning,
    stale_input_warning,
)
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths, load_project_json
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.storage.state_db import StateDB

MISSING_BINDING_HELP = 'Please ensure you have connected an input or set a default value.'


def _format_contract_error_details(issues: list[object]) -> str | None:
    for issue in issues:
        if getattr(issue, 'severity', None) != ValidationSeverity.ERROR:
            continue
        message = str(getattr(issue, 'message', '') or '').strip()
        details = getattr(issue, 'details', {}) or {}
        if not message:
            continue
        location_bits: list[str] = []
        cell_number = details.get('cell_number')
        line = details.get('line')
        line_in_cell = details.get('line_in_cell')
        if isinstance(cell_number, int):
            location_bits.append(f'cell {cell_number}')
        if isinstance(line, int):
            location_bits.append(f'line {line}')
        elif isinstance(line_in_cell, int):
            location_bits.append(f'line {line_in_cell}')
        location = f' ({", ".join(location_bits)})' if location_bits and 'line ' not in message.lower() else ''
        snippet = details.get('source_line') or details.get('source')
        if 'offending code:' in message.lower():
            return f'{message}{location}'
        if isinstance(snippet, str) and snippet.strip():
            return f'{message}{location} Offending code: `{snippet.strip()}`.'
        return f'{message}{location}'
    return None


def _contract_refresh_error(action: str, *, node_id: str, issues: list[object]) -> KeyError:
    details = _format_contract_error_details(issues)
    message = f'Cannot {action} because notebook `{node_id}` currently has validation errors and could not be reparsed.'
    if details:
        message = f'{message} {details}'
    return KeyError(message)


@dataclass(slots=True)
class Binding:
    source_node: str
    source_artifact: str
    data_type: str
    default: Any = None
    has_default: bool = False


@dataclass(slots=True)
class RuntimeContext:
    project_root: Path
    node_id: str
    run_id: str
    source_hash: str
    lineage_mode: LineageMode
    bindings: dict[str, Binding]
    outputs: dict[str, Port]
    asset_declarations: dict[str, AssetDeclaration] = field(default_factory=dict)
    project_id: str | None = None
    db: StateDB = field(init=False)
    paths: ProjectPaths = field(init=False)
    object_store: ObjectStore = field(init=False)
    loaded_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pushed_outputs: list[dict[str, Any]] = field(default_factory=list)
    pushed_assets: list[dict[str, Any]] = field(default_factory=list)
    interactive_contract_key: tuple[float | None, str] | None = None
    interactive_contract_error: KeyError | None = None
    defer_publication: bool = False
    publication_id: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.paths = ProjectPaths(self.project_root)
        self.db = StateDB(self.paths.state_db_path)
        self.object_store = ObjectStore(self.paths)
        if self.project_id is None:
            self.project_id = str(load_project_json(self.paths)['project_id'])
        incarnation = self.db.live_incarnation(self.node_id)
        if incarnation is not None:
            graph_version = int(GraphStore(self.paths).read().meta.get('graph_version', 0))
            publication = self.db.begin_publication(
                run_id=self.run_id,
                node_id=self.node_id,
                source_hash=self.source_hash,
                graph_version=graph_version,
            )
            self.publication_id = str(publication['publication_id'])

    def resolve_pull(self, name: str) -> dict[str, Any]:
        self._refresh_interactive_contracts()
        binding = self.bindings.get(name)
        if binding is None:
            if self.interactive_contract_error is not None:
                raise self.interactive_contract_error
            raise KeyError(f'No binding configured for input `{name}`.')
        if not binding.source_node:
            if binding.has_default:
                return {
                    'value': binding.default,
                    'artifact_hash': hash_json(binding.default),
                    'upstream_code_hash': 'default',
                    'state': ArtifactState.READY.value,
                    'warnings': [],
                    'source_node': '',
                    'source_artifact': '',
                    'loaded_version_id': None,
                }
            raise FileNotFoundError(f'Artifact binding for `{name}` is missing. {MISSING_BINDING_HELP}')
        head = self.db.get_artifact_head(binding.source_node, binding.source_artifact)
        if head is None or head['current_version_id'] is None:
            raise FileNotFoundError(f'Artifact `{binding.source_node}/{binding.source_artifact}` is pending.')
        if head['data_type'] != binding.data_type:
            raise TypeError(
                f'Artifact type mismatch for `{binding.source_node}/{binding.source_artifact}`: '
                f'expected {binding.data_type}, got {head["data_type"]}.'
            )
        self.db.touch_artifact_object(head['artifact_hash'])
        self._lease_for_run(str(head['artifact_hash']))
        warnings: list[dict[str, Any]] = []
        if head['state'] == ArtifactState.STALE.value:
            warnings.append(stale_input_warning(f'{binding.source_node}/{binding.source_artifact}'))
        return {
            'value': self.object_store.load_value(head['artifact_hash'], binding.data_type),
            'artifact_hash': head['artifact_hash'],
            'upstream_code_hash': head['upstream_code_hash'],
            'state': head['state'],
            'warnings': warnings,
            'source_node': binding.source_node,
            'source_artifact': binding.source_artifact,
            'loaded_version_id': head['current_version_id'],
            'producer_incarnation_id': self.db.live_incarnation_id(binding.source_node),
        }

    def validate_pull_contract(self, *, name: str, data_type: str) -> None:
        self._refresh_interactive_contracts()
        binding = self.bindings.get(name)
        if binding is None:
            if self.interactive_contract_error is not None:
                raise self.interactive_contract_error
            raise KeyError(f'No binding configured for input `{name}`.')
        if binding.data_type != data_type:
            raise TypeError(f'Input contract mismatch for `{name}`: expected {binding.data_type}, got {data_type}.')

    def resolve_pull_file(self, name: str, allow_missing: bool = False) -> dict[str, Any]:
        self._refresh_interactive_contracts()
        binding = self.bindings.get(name)
        if binding is None:
            if self.interactive_contract_error is not None:
                raise self.interactive_contract_error
            raise KeyError(f'No binding configured for file input `{name}`.')
        if binding.data_type != 'file':
            raise TypeError(f'Input contract mismatch for `{name}`: expected {binding.data_type}, got file.')
        if not binding.source_node:
            if binding.has_default or allow_missing:
                return {
                    'path': None,
                    'artifact_hash': hash_json(binding.default),
                    'upstream_code_hash': 'default',
                    'state': ArtifactState.READY.value,
                    'warnings': [],
                    'source_node': '',
                    'source_artifact': '',
                    'loaded_version_id': None,
                }
            raise FileNotFoundError(f'Artifact binding for `{name}` is missing. {MISSING_BINDING_HELP}')
        head = self.db.get_artifact_head(binding.source_node, binding.source_artifact)
        if head is None or head['current_version_id'] is None:
            raise FileNotFoundError(f'Artifact `{binding.source_node}/{binding.source_artifact}` is pending.')
        self.db.touch_artifact_object(head['artifact_hash'])
        self._lease_for_run(str(head['artifact_hash']))
        warnings: list[dict[str, Any]] = []
        if head['state'] == ArtifactState.STALE.value:
            warnings.append(stale_input_warning(f'{binding.source_node}/{binding.source_artifact}'))
        return {
            'path': self.object_store.load_file_path(head['artifact_hash'], extension=head.get('extension')),
            'artifact_hash': head['artifact_hash'],
            'upstream_code_hash': head['upstream_code_hash'],
            'state': head['state'],
            'warnings': warnings,
            'source_node': binding.source_node,
            'source_artifact': binding.source_artifact,
            'loaded_version_id': head['current_version_id'],
            'producer_incarnation_id': self.db.live_incarnation_id(binding.source_node),
        }

    def record_pull(self, name: str, metadata: dict[str, Any]) -> None:
        self.loaded_inputs[name] = metadata
        source_node = str(metadata.get('source_node') or '')
        source_artifact = str(metadata.get('source_artifact') or '')
        self.db.record_run_input(
            self.run_id,
            f'{source_node}/{source_artifact}' if source_node else f'default:{self.node_id}/{name}',
            metadata['artifact_hash'],
            metadata['state'],
            producer_incarnation_id=metadata.get('producer_incarnation_id'),
            producer_artifact_name=source_artifact or None,
            version_id=metadata.get('loaded_version_id'),
        )

    def _lease_for_run(self, artifact_hash: str) -> None:
        self.db.acquire_object_lease(
            artifact_hash,
            'runtime_pull',
            f'{self.run_id}:{uuid.uuid4()}',
            expires_at=(datetime.now(tz=UTC) + timedelta(hours=24)).isoformat().replace('+00:00', 'Z'),
        )

    def finalize_value_push(self, *, name: str, value: Any, data_type: str, role: ArtifactRole) -> dict[str, Any]:
        self._refresh_interactive_contracts()
        if self.interactive_contract_error is not None:
            raise self.interactive_contract_error
        self._validate_output_contract(name=name, data_type=data_type, role=role, kind='value')
        persisted = self.object_store.persist_value(value, data_type)
        self.db.upsert_artifact_object(
            persisted['artifact_hash'],
            persisted['storage_kind'],
            persisted['data_type'],
            persisted['size_bytes'],
            persisted.get('extension'),
            persisted.get('mime_type'),
            persisted.get('preview'),
        )
        return self._create_version(name=name, persisted=persisted, role=role)

    def finalize_file_push(self, *, name: str, temp_path: Path, role: ArtifactRole) -> dict[str, Any]:
        self._refresh_interactive_contracts()
        if self.interactive_contract_error is not None:
            raise self.interactive_contract_error
        self._validate_output_contract(name=name, data_type='file', role=role, kind='file')
        persisted = self.object_store.persist_file(temp_path)
        self.db.upsert_artifact_object(
            persisted['artifact_hash'],
            persisted['storage_kind'],
            persisted['data_type'],
            persisted['size_bytes'],
            persisted.get('extension'),
            persisted.get('mime_type'),
            persisted.get('preview'),
        )
        return self._create_version(name=name, persisted=persisted, role=role)

    def finalize_asset_push(
        self,
        *,
        asset: BaseAsset,
        name: str,
        title: str,
        description: str | None,
        asset_type: type[BaseAsset] | None,
    ) -> dict[str, Any]:
        self._refresh_interactive_contracts()
        self._ensure_publication()
        if self.interactive_contract_error is not None:
            raise self.interactive_contract_error
        declaration = self.asset_declarations.get(name)
        if declaration is None:
            raise KeyError(f'Asset `{name}` is not declared in the parsed notebook contract.')
        if declaration.title != title:
            raise TypeError(f'Asset title mismatch for `{name}`: expected {declaration.title!r}, got {title!r}.')
        if declaration.description != description:
            raise TypeError(
                f'Asset description mismatch for `{name}`: expected {declaration.description!r}, got {description!r}.'
            )
        runtime_asset_type = asset_type_id_for_instance(asset)
        if runtime_asset_type is None:
            raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')
        runtime_declared_type = asset_type_id_for_class(asset_type) if asset_type is not None else None
        if asset_type is not None and runtime_declared_type is None:
            raise TypeError('asset_type must be a BulletJournal asset class reference such as `assets.Markdown`.')
        if declaration.declared_asset_type is not None and declaration.declared_asset_type != runtime_asset_type:
            raise TypeError(
                'Asset type mismatch for '
                f'`{name}`: expected {declaration.declared_asset_type}, got {runtime_asset_type}.'
            )
        if runtime_declared_type is not None and runtime_declared_type != runtime_asset_type:
            raise TypeError(
                f'Asset type mismatch for `{name}`: expected {runtime_declared_type}, got {runtime_asset_type}.'
            )
        serialized = serialize_asset(asset, object_store=self.object_store, title=title, description=description)
        objects: list[dict[str, Any]] = []
        for item in serialized.objects:
            persisted = item.persisted
            self.db.upsert_artifact_object(
                persisted['artifact_hash'],
                persisted['storage_kind'],
                persisted['data_type'],
                persisted['size_bytes'],
                persisted.get('extension'),
                persisted.get('mime_type'),
                persisted.get('preview'),
            )
            objects.append(
                {
                    'object_role': item.object_role,
                    'object_index': item.object_index,
                    'artifact_hash': persisted['artifact_hash'],
                    'metadata': item.metadata,
                }
            )
        upstream_data_hash, upstream_code_hash, warnings, output_state = self._lineage_for_logical_output(name)
        override_schema_hash = hash_json(serialized.modifier_schema)
        asset_version_id = self.db.create_asset_version(
            node_id=self.node_id,
            asset_name=name,
            asset_type=serialized.asset_type,
            interactive=serialized.interactive,
            source_hash=self.source_hash,
            upstream_code_hash=upstream_code_hash,
            upstream_data_hash=upstream_data_hash,
            run_id=self.run_id,
            lineage_mode=self.lineage_mode,
            definition=serialized.definition,
            modifier_schema=serialized.modifier_schema,
            default_modifiers=serialized.default_modifiers,
            override_schema_hash=override_schema_hash,
            warnings=warnings,
            objects=objects,
            state=output_state,
            publication_id=self.publication_id,
        )
        record = {
            'asset_name': name,
            'asset_version_id': asset_version_id,
            'asset_type': serialized.asset_type,
            'state': output_state.value,
        }
        self.pushed_assets.append(record)
        if self.publication_id is not None and not self.defer_publication:
            self.commit_publication()
        return record

    def _create_version(self, *, name: str, persisted: dict[str, Any], role: ArtifactRole) -> dict[str, Any]:
        self._ensure_publication()
        upstream_data_hash, upstream_code_hash, warnings, output_state = self._lineage_for_logical_output(name)
        version_id = self.db.create_artifact_version(
            node_id=self.node_id,
            artifact_name=name,
            role=role,
            artifact_hash=persisted['artifact_hash'],
            source_hash=self.source_hash,
            upstream_code_hash=upstream_code_hash,
            upstream_data_hash=upstream_data_hash,
            run_id=self.run_id,
            lineage_mode=self.lineage_mode,
            warnings=warnings,
            state=output_state,
            publication_id=self.publication_id,
        )
        record = {
            'artifact_name': name,
            'version_id': version_id,
            'artifact_hash': persisted['artifact_hash'],
            'state': output_state.value,
            'role': role.value,
        }
        self.pushed_outputs.append(record)
        if self.publication_id is not None and not self.defer_publication:
            self.commit_publication()
        return record

    def commit_publication(self, *, execution_head: dict[str, Any] | None = None) -> bool:
        if self.publication_id is None:
            return True
        graph = GraphStore(self.paths).read()
        current_node = next((node for node in graph.nodes if node.id == self.node_id), None)
        current_source_hash = self.source_hash
        notebook_path = self.paths.notebook_path(self.node_id)
        if notebook_path.exists():
            current_source_hash = compute_source_hash(notebook_path)
        downstream = _downstream_node_ids(graph, self.node_id)
        with ProjectLock(self.paths.project_lock_path).exclusive():
            committed = self.db.commit_publication(
                self.publication_id,
                current_source_hash=current_source_hash if current_node is not None else '',
                downstream_node_ids=downstream,
                execution_head=execution_head,
            )
        if not committed:
            raise RuntimeError('Publication was superseded by a newer node generation or input version.')
        if not self.defer_publication:
            self.publication_id = None
        return True

    def abandon_publication(self) -> None:
        if self.publication_id is not None:
            self.db.abandon_publication(self.publication_id)

    def _ensure_publication(self) -> None:
        if self.publication_id is not None:
            return
        incarnation = self.db.live_incarnation(self.node_id)
        if incarnation is None:
            return
        graph_version = int(GraphStore(self.paths).read().meta.get('graph_version', 0))
        publication = self.db.begin_publication(
            run_id=self.run_id,
            node_id=self.node_id,
            source_hash=self.source_hash,
            graph_version=graph_version,
        )
        self.publication_id = str(publication['publication_id'])

    def _lineage_for_logical_output(self, name: str) -> tuple[str, str, list[dict[str, Any]], ArtifactState]:
        input_hashes = [self.source_hash, f'{self.node_id}/{name}']
        input_code_hashes = [self.source_hash, f'{self.node_id}/{name}']
        warnings: list[dict[str, Any]] = []
        warning_keys: set[str] = set()
        output_state = ArtifactState.READY
        for metadata in self.loaded_inputs.values():
            input_hashes.append(metadata['artifact_hash'])
            input_code_hashes.append(metadata['upstream_code_hash'])
            for warning in metadata['warnings']:
                warning_key = json.dumps(warning, sort_keys=True)
                if warning_key in warning_keys:
                    continue
                warning_keys.add(warning_key)
                warnings.append(warning)
            if metadata['state'] == ArtifactState.STALE.value:
                output_state = ArtifactState.STALE
            source_node = metadata.get('source_node')
            source_artifact = metadata.get('source_artifact')
            loaded_version_id = metadata.get('loaded_version_id')
            if not isinstance(source_node, str) or not isinstance(source_artifact, str) or not source_node:
                continue
            logical_artifact_id = f'{source_node}/{source_artifact}'
            head = self.db.get_artifact_head(source_node, source_artifact)
            if head is None or head.get('current_version_id') is None:
                output_state = ArtifactState.STALE
                continue
            if head.get('current_version_id') != loaded_version_id:
                output_state = ArtifactState.STALE
                warning = outdated_input_warning(logical_artifact_id)
                warning_key = json.dumps(warning, sort_keys=True)
                if warning_key in warning_keys:
                    continue
                warning_keys.add(warning_key)
                warnings.append(warning)
                continue
            if head['state'] == ArtifactState.STALE.value:
                output_state = ArtifactState.STALE
                warning = stale_input_warning(logical_artifact_id)
                warning_key = json.dumps(warning, sort_keys=True)
                if warning_key in warning_keys:
                    continue
                warning_keys.add(warning_key)
                warnings.append(warning)
        if self.lineage_mode == LineageMode.INTERACTIVE_HEURISTIC:
            warnings.append(interactive_lineage_warning())
        upstream_data_hash = combine_hashes(input_hashes)
        upstream_code_hash = combine_hashes(input_code_hashes)
        return upstream_data_hash, upstream_code_hash, warnings, output_state

    def _validate_output_contract(self, *, name: str, data_type: str, role: ArtifactRole, kind: str) -> None:
        expected = self.outputs.get(name)
        if expected is None:
            raise KeyError(f'Output `{name}` is not declared in the parsed notebook interface.')
        expected_role = expected.role
        if expected_role is None:
            raise TypeError(f'Output `{name}` is missing a declared role in the parsed notebook interface.')
        if expected_role != role:
            raise TypeError(f'Output role mismatch for `{name}`: expected {expected_role.value}, got {role.value}.')
        if expected.data_type != data_type:
            raise TypeError(f'Output type mismatch for `{name}`: expected {expected.data_type}, got {data_type}.')
        expected_kind = expected.kind or 'value'
        if expected_kind != kind:
            raise TypeError(f'Output kind mismatch for `{name}`: expected {expected_kind}, got {kind}.')

    def _stabilize_if_interactive(self) -> None:
        if self.lineage_mode != LineageMode.INTERACTIVE_HEURISTIC:
            return
        notebook_path = self.paths.notebook_path(self.node_id)
        stable_for = 0.0
        previous_mtime = notebook_path.stat().st_mtime if notebook_path.exists() else None
        while stable_for < EDIT_STABILIZATION_SECONDS:
            time.sleep(0.2)
            current_mtime = notebook_path.stat().st_mtime if notebook_path.exists() else None
            if current_mtime == previous_mtime:
                stable_for += 0.2
            else:
                previous_mtime = current_mtime
                stable_for = 0.0
        if notebook_path.exists():
            self.source_hash = compute_source_hash(notebook_path)

    def _refresh_interactive_contracts(self) -> None:
        if self.lineage_mode != LineageMode.INTERACTIVE_HEURISTIC:
            return
        current_key = self._interactive_contract_key_for_current_state()
        if current_key == self.interactive_contract_key:
            return
        self._stabilize_if_interactive()
        notebook_path = self.paths.notebook_path(self.node_id)
        if not notebook_path.exists():
            return
        graph = GraphStore(self.paths).read()
        current_key = _interactive_contract_key(notebook_path, graph.meta)
        if current_key == self.interactive_contract_key:
            return
        contract = parse_notebook_contract(notebook_path, node_id=self.node_id)
        if any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues):
            self.interactive_contract_key = current_key
            self.interactive_contract_error = _contract_refresh_error(
                'refresh runtime bindings',
                node_id=self.node_id,
                issues=contract.issues,
            )
            return
        self.source_hash = contract.source_hash
        self.bindings = _live_bindings_for_node(graph, contract.interface.inputs, node_id=self.node_id)
        self.outputs = {port.name: port for port in contract.interface.outputs}
        self.asset_declarations = {declaration.name: declaration for declaration in contract.asset_declarations}
        self.interactive_contract_key = current_key
        self.interactive_contract_error = None

    def _interactive_contract_key_for_current_state(self) -> tuple[float | None, str]:
        notebook_path = self.paths.notebook_path(self.node_id)
        graph = GraphStore(self.paths).read()
        return _interactive_contract_key(notebook_path, graph.meta)


def _interactive_contract_key(notebook_path: Path, graph_meta: dict[str, Any]) -> tuple[float | None, str]:
    notebook_mtime = notebook_path.stat().st_mtime if notebook_path.exists() else None
    return (notebook_mtime, str(graph_meta.get('updated_at') or ''))


def _live_bindings_for_node(
    graph: GraphData,
    inputs: list[Port],
    *,
    node_id: str,
) -> dict[str, Binding]:
    bindings: dict[str, Binding] = {}
    for port in inputs:
        binding = resolve_input_binding(graph, node_id=node_id, input_name=port.name)
        if binding is None:
            bindings[port.name] = Binding(
                source_node='',
                source_artifact='',
                data_type=port.data_type,
                default=port.default,
                has_default=port.has_default,
            )
            continue
        bindings[port.name] = Binding(
            source_node=binding[0],
            source_artifact=binding[1],
            data_type=port.data_type,
            default=port.default,
            has_default=port.has_default,
        )
    return bindings


def _downstream_node_ids(graph: GraphData, node_id: str) -> list[str]:
    pending = [node_id]
    seen: set[str] = set()
    while pending:
        source = pending.pop()
        for edge in graph.edges:
            if edge.source_node != source or edge.target_node in seen or edge.target_node == node_id:
                continue
            seen.add(edge.target_node)
            pending.append(edge.target_node)
    return sorted(seen)


_RUNTIME_CONTEXT: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    'bulletjournal_runtime_context', default=None
)


@contextlib.contextmanager
def activate_runtime_context(context: RuntimeContext):
    token = _RUNTIME_CONTEXT.set(context)
    try:
        yield context
    finally:
        _RUNTIME_CONTEXT.reset(token)


def current_runtime_context() -> RuntimeContext:
    current = _RUNTIME_CONTEXT.get()
    if current is not None:
        return current
    env_root = os.environ.get('BULLETJOURNAL_PROJECT_ROOT')
    env_node = os.environ.get('BULLETJOURNAL_NODE_ID')
    env_run = os.environ.get('BULLETJOURNAL_RUN_ID')
    env_source_hash = os.environ.get('BULLETJOURNAL_SOURCE_HASH')
    env_lineage = os.environ.get('BULLETJOURNAL_LINEAGE_MODE')
    env_bindings = os.environ.get('BULLETJOURNAL_BINDINGS_JSON')
    env_outputs = os.environ.get('BULLETJOURNAL_OUTPUTS_JSON')
    env_assets = os.environ.get('BULLETJOURNAL_ASSET_DECLARATIONS_JSON')
    if not all([env_root, env_node, env_run, env_source_hash, env_lineage]):
        raise RuntimeError('BulletJournal runtime context is not active.')
    binding_data = json.loads(env_bindings) if env_bindings else {}
    output_data = json.loads(env_outputs) if env_outputs else {}
    asset_data = json.loads(env_assets) if env_assets else {}
    context = RuntimeContext(
        project_root=Path(env_root),
        node_id=env_node,
        run_id=env_run,
        source_hash=env_source_hash,
        lineage_mode=LineageMode(env_lineage),
        bindings={name: Binding(**value) for name, value in binding_data.items()},
        outputs={
            name: Port(
                name=name,
                data_type=value['data_type'],
                role=ArtifactRole(value['role']) if value.get('role') else None,
                description=value.get('description'),
                kind=value.get('kind', 'value'),
                direction=value.get('direction', 'output'),
            )
            for name, value in output_data.items()
        },
        asset_declarations={
            name: AssetDeclaration(
                node_id=value.get('node_id', env_node),
                name=name,
                title=value['title'],
                description=value.get('description'),
                declared_asset_type=value.get('declared_asset_type'),
                declaration_index=int(value.get('declaration_index', 0)),
            )
            for name, value in asset_data.items()
        },
    )
    _RUNTIME_CONTEXT.set(context)
    return context


def get_node_id() -> str:
    return current_runtime_context().node_id


def get_project_id() -> str:
    project_id = current_runtime_context().project_id
    if project_id is None:
        raise RuntimeError('BulletJournal runtime context did not resolve a project id.')
    return project_id
