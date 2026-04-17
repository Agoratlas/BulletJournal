from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from bulletjournal.domain.enums import LineageMode, NodeKind, ValidationSeverity
from bulletjournal.domain.models import Edge, GraphData, NotebookInterface, Port
from bulletjournal.execution.planner import downstream_closure
from bulletjournal.parser.interface_parser import parse_notebook_interface
from bulletjournal.runtime.context import Binding, RuntimeContext, activate_runtime_context
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.project_fs import ProjectPaths, is_project_root
from bulletjournal.utils import utc_now_iso


class StandaloneRuntimeError(RuntimeError):
    pass


def run_notebook_app(app: Any, notebook_path: str | Path):
    context = build_standalone_context(notebook_path)
    _record_run_started(context)
    try:
        with activate_runtime_context(context):
            result = app.run()
    except Exception as exc:
        _record_run_finished(context, failure=exc)
        raise
    _record_run_finished(context)
    _mark_downstream_stale(context)
    return result


def build_standalone_context(notebook_path: str | Path) -> RuntimeContext:
    resolved_notebook = Path(notebook_path).resolve()
    if not resolved_notebook.exists():
        raise StandaloneRuntimeError(f'Notebook path does not exist: {resolved_notebook}')
    project_paths = _discover_project_paths(resolved_notebook)
    graph = GraphStore(project_paths).read()
    node_id = resolved_notebook.stem
    _require_notebook_node(graph, node_id=node_id)
    expected_path = project_paths.notebook_path(node_id)
    if resolved_notebook != expected_path:
        raise StandaloneRuntimeError(
            f'Notebook `{resolved_notebook}` does not match project node path `{expected_path}`.'
        )
    interface = parse_notebook_interface(resolved_notebook, node_id=node_id)
    error_messages = [issue.message for issue in interface.issues if issue.severity == ValidationSeverity.ERROR]
    if error_messages:
        joined = '; '.join(error_messages)
        raise StandaloneRuntimeError(f'Notebook `{node_id}` has validation errors: {joined}')
    return RuntimeContext(
        project_root=project_paths.root,
        node_id=node_id,
        run_id=f'standalone-{uuid.uuid4()}',
        source_hash=interface.source_hash,
        lineage_mode=LineageMode.MANAGED,
        bindings=_bindings_for_interface(graph, interface),
        outputs=_outputs_for_interface(interface),
    )


def _discover_project_paths(notebook_path: Path) -> ProjectPaths:
    for parent in notebook_path.parents:
        if is_project_root(parent):
            return ProjectPaths(parent.resolve())
    raise StandaloneRuntimeError(
        'Standalone notebook execution requires a notebook inside an BulletJournal project root.'
    )


def _require_notebook_node(graph: GraphData, *, node_id: str):
    for node in graph.nodes:
        if node.id == node_id:
            if node.kind != NodeKind.NOTEBOOK:
                raise StandaloneRuntimeError(f'Node `{node_id}` is not a notebook node.')
            return node
    raise StandaloneRuntimeError(f'No notebook node with id `{node_id}` exists in this project.')


def _bindings_for_interface(graph: GraphData, interface: NotebookInterface) -> dict[str, Binding]:
    return {port.name: _binding_for_port(graph.edges, interface.node_id, port) for port in interface.inputs}


def _binding_for_port(edges: list[Edge], node_id: str, port: Port) -> Binding:
    for edge in edges:
        if edge.target_node == node_id and edge.target_port == port.name:
            return Binding(
                source_node=edge.source_node,
                source_artifact=edge.source_port,
                data_type=port.data_type,
                default=port.default,
                has_default=port.has_default,
            )
    return Binding(
        source_node='',
        source_artifact='',
        data_type=port.data_type,
        default=port.default,
        has_default=port.has_default,
    )


def _outputs_for_interface(interface: NotebookInterface) -> dict[str, Port]:
    return {port.name: port for port in [*interface.outputs, *interface.assets]}


def _record_run_started(context: RuntimeContext) -> None:
    graph_store = GraphStore(context.paths)
    graph = graph_store.read()
    context.db.record_run(
        context.run_id,
        _project_id(graph),
        'standalone',
        {'node_id': context.node_id},
        int(graph.meta['graph_version']),
        {'started_at': utc_now_iso(), 'entrypoint': 'standalone'},
    )
    from bulletjournal.domain.enums import RunStatus

    context.db.update_run_status(context.run_id, RunStatus.RUNNING)


def _record_run_finished(context: RuntimeContext, failure: Exception | None = None) -> None:
    from bulletjournal.domain.enums import RunStatus

    if failure is None:
        context.db.update_run_status(context.run_id, RunStatus.SUCCEEDED)
        return
    context.db.update_run_status(
        context.run_id,
        RunStatus.FAILED,
        failure_json={'error': str(failure)},
    )


def _mark_downstream_stale(context: RuntimeContext) -> None:
    graph = GraphStore(context.paths).read()
    for downstream_node in downstream_closure(graph, context.node_id):
        interface = parse_notebook_interface(
            context.paths.notebook_path(downstream_node),
            node_id=downstream_node,
        )
        for port in [*interface.outputs, *interface.assets]:
            head = context.db.get_artifact_head(downstream_node, port.name)
            if head and head['current_version_id'] is not None:
                from bulletjournal.domain.enums import ArtifactState

                context.db.set_artifact_head_state(
                    downstream_node,
                    port.name,
                    ArtifactState.STALE,
                )


def _project_id(graph: GraphData) -> str:
    return str(graph.meta.get('project_id', 'bulletjournal-project'))
