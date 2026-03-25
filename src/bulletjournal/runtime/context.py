from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bulletjournal.config import EDIT_STABILIZATION_SECONDS
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, ValidationSeverity
from bulletjournal.domain.hashing import combine_hashes, hash_json
from bulletjournal.domain.models import Port
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.parser.source_hash import compute_source_hash
from bulletjournal.runtime.warnings import interactive_lineage_warning, stale_input_warning
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths
from bulletjournal.storage.state_db import StateDB


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
    db: StateDB = field(init=False)
    paths: ProjectPaths = field(init=False)
    object_store: ObjectStore = field(init=False)
    loaded_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pushed_outputs: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.paths = ProjectPaths(self.project_root)
        self.db = StateDB(self.paths.state_db_path)
        self.object_store = ObjectStore(self.paths)

    def resolve_pull(self, name: str) -> dict[str, Any]:
        binding = self.bindings.get(name)
        if binding is None:
            raise KeyError(f'No binding configured for input `{name}`.')
        if not binding.source_node:
            if binding.has_default:
                return {
                    'value': binding.default,
                    'artifact_hash': hash_json(binding.default),
                    'upstream_code_hash': 'default',
                    'state': ArtifactState.READY.value,
                    'warnings': [],
                }
            raise FileNotFoundError(f'Artifact binding for `{name}` is missing.')
        head = self.db.get_artifact_head(binding.source_node, binding.source_artifact)
        if head is None or head['current_version_id'] is None:
            raise FileNotFoundError(f'Artifact `{binding.source_node}/{binding.source_artifact}` is pending.')
        if head['data_type'] != binding.data_type:
            raise TypeError(
                f'Artifact type mismatch for `{binding.source_node}/{binding.source_artifact}`: '
                f'expected {binding.data_type}, got {head["data_type"]}.'
            )
        self.db.touch_artifact_object(head['artifact_hash'])
        warnings: list[dict[str, Any]] = []
        if head['state'] == ArtifactState.STALE.value:
            warnings.append(stale_input_warning(f'{binding.source_node}/{binding.source_artifact}'))
        return {
            'value': self.object_store.load_value(head['artifact_hash'], binding.data_type),
            'artifact_hash': head['artifact_hash'],
            'upstream_code_hash': head['upstream_code_hash'],
            'state': head['state'],
            'warnings': warnings,
        }

    def validate_pull_contract(self, *, name: str, data_type: str) -> None:
        binding = self.bindings.get(name)
        if binding is None:
            raise KeyError(f'No binding configured for input `{name}`.')
        if binding.data_type != data_type:
            raise TypeError(f'Input contract mismatch for `{name}`: expected {binding.data_type}, got {data_type}.')

    def resolve_pull_file(self, name: str, allow_missing: bool = False) -> dict[str, Any]:
        binding = self.bindings.get(name)
        if binding is None:
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
                }
            raise FileNotFoundError(f'Artifact binding for `{name}` is missing.')
        head = self.db.get_artifact_head(binding.source_node, binding.source_artifact)
        if head is None or head['current_version_id'] is None:
            raise FileNotFoundError(f'Artifact `{binding.source_node}/{binding.source_artifact}` is pending.')
        self.db.touch_artifact_object(head['artifact_hash'])
        warnings: list[dict[str, Any]] = []
        if head['state'] == ArtifactState.STALE.value:
            warnings.append(stale_input_warning(f'{binding.source_node}/{binding.source_artifact}'))
        return {
            'path': self.object_store.load_file_path(head['artifact_hash']),
            'artifact_hash': head['artifact_hash'],
            'upstream_code_hash': head['upstream_code_hash'],
            'state': head['state'],
            'warnings': warnings,
        }

    def record_pull(self, name: str, metadata: dict[str, Any]) -> None:
        self.loaded_inputs[name] = metadata
        self.db.record_run_input(self.run_id, f'{self.node_id}/{name}', metadata['artifact_hash'], metadata['state'])

    def finalize_value_push(self, *, name: str, value: Any, data_type: str, role: ArtifactRole) -> dict[str, Any]:
        self._refresh_interactive_contracts()
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

    def _create_version(self, *, name: str, persisted: dict[str, Any], role: ArtifactRole) -> dict[str, Any]:
        input_hashes = [self.source_hash, f'{self.node_id}/{name}']
        input_code_hashes = [self.source_hash, f'{self.node_id}/{name}']
        warnings = []
        output_state = ArtifactState.READY
        for metadata in self.loaded_inputs.values():
            input_hashes.append(metadata['artifact_hash'])
            input_code_hashes.append(metadata['upstream_code_hash'])
            warnings.extend(metadata['warnings'])
            if metadata['state'] == ArtifactState.STALE.value:
                output_state = ArtifactState.STALE
        if self.lineage_mode == LineageMode.INTERACTIVE_HEURISTIC:
            warnings.append(interactive_lineage_warning())
        upstream_data_hash = combine_hashes(input_hashes)
        upstream_code_hash = combine_hashes(input_code_hashes)
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
        )
        record = {
            'artifact_name': name,
            'version_id': version_id,
            'artifact_hash': persisted['artifact_hash'],
            'state': output_state.value,
            'role': role.value,
        }
        self.pushed_outputs.append(record)
        return record

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
        self._stabilize_if_interactive()
        notebook_path = self.paths.notebook_path(self.node_id)
        if not notebook_path.exists():
            return
        interface = parse_notebook_interface(notebook_path, node_id=self.node_id)
        if any(issue.severity == ValidationSeverity.ERROR for issue in interface.issues):
            return
        self.source_hash = interface.source_hash
        self.outputs = {port.name: port for port in [*interface.outputs, *interface.assets]}


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
    if not all([env_root, env_node, env_run, env_source_hash, env_lineage]):
        raise RuntimeError('BulletJournal runtime context is not active.')
    assert env_root is not None
    assert env_node is not None
    assert env_run is not None
    assert env_source_hash is not None
    assert env_lineage is not None
    root = env_root
    node_id = env_node
    run_id = env_run
    source_hash = env_source_hash
    lineage_mode = env_lineage
    binding_data = json.loads(env_bindings) if env_bindings else {}
    output_data = json.loads(env_outputs) if env_outputs else {}
    context = RuntimeContext(
        project_root=Path(root),
        node_id=node_id,
        run_id=run_id,
        source_hash=source_hash,
        lineage_mode=LineageMode(lineage_mode),
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
    )
    _RUNTIME_CONTEXT.set(context)
    return context
