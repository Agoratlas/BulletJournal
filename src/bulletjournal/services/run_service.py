from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, cast

from bulletjournal.config import ServerConfig, normalize_base_path
from bulletjournal.domain.enums import ArtifactState, LineageMode, NodeKind, RunMode, RunStatus, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError, RunConflictError
from bulletjournal.domain.graph_bindings import resolve_input_binding
from bulletjournal.domain.models import GraphData
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.planner import (
    downstream_closure,
    run_plan_for_node,
    stale_or_pending_nodes,
    topological_nodes,
    upstream_closure,
)
from bulletjournal.execution.runner import WorkerRunner
from bulletjournal.execution.sessions import SessionManager
from bulletjournal.parser.source_hash import compute_source_hash
from bulletjournal.parser.validation import build_issue_id
from bulletjournal.services.notebook_freshness import lineage_metadata_for_notebook, notebook_uses_execution_head
from bulletjournal.utils import utc_now_iso


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    cancel_event: Event
    node_ids: list[str]
    current_node: str | None = None
    current_node_started_at: str | None = None
    current_node_started_monotonic: float | None = None
    process: object | None = None
    cancel_reason: str | None = None


@dataclass(slots=True)
class OrchestratorNodeState:
    node_id: str
    incarnation_id: str
    run_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None


def _format_markdown_code(value: str) -> str:
    sanitized = value.replace('`', "'")
    return f'`{sanitized}`'


def _describe_node_label(title: str, node_id: str) -> str:
    if title == node_id:
        return _format_markdown_code(node_id)
    return f'{_format_markdown_code(title)} ({_format_markdown_code(node_id)})'


RUNTIME_NOTICE_CODES = ['run_failed', 'run_warning']


class RunService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self.worker_runner = WorkerRunner()
        self.session_manager = SessionManager()
        self.server_config: ServerConfig | None = None
        self._lock = Lock()
        self._active_run: ActiveRun | None = None
        self._orchestrator_node_states: dict[str, OrchestratorNodeState] = {}
        self._editor_session_output_snapshots: dict[
            str,
            dict[str, dict[str, tuple[int | None, str]]],
        ] = {}
        self._session_ready_watchers: set[str] = set()

    def has_active_run(self) -> bool:
        with self._lock:
            return self._active_run is not None

    def start_node_run(
        self,
        node_id: str,
        *,
        mode: str,
        action: str | None = None,
        scope: str = 'node',
    ) -> dict[str, Any]:
        self.project_service.require_project()
        run_mode = RunMode(mode)
        node = self.project_service.get_node(node_id)
        if node.kind in {NodeKind.CONSTANT, NodeKind.FILE_INPUT, NodeKind.ORGANIZER, NodeKind.AREA, NodeKind.DASHBOARD}:
            return {'status': 'noop', 'node_id': node_id}
        if run_mode == RunMode.EDIT_RUN:
            return self._start_edit_session(node_id)
        plan = self._effective_run_plan(self._plan_for_scope(node_id, scope=scope))
        pending = self._preflight_plan(plan)
        blocked_nodes = cast(list[dict[str, Any]], pending['blocked_nodes'])
        if blocked_nodes and action is None:
            return {'requires_confirmation': True, **pending}
        if action == 'use_stale':
            unresolved = self._blocked_nodes_with_unresolved_inputs(blocked_nodes, allow_stale_only=True)
            if unresolved:
                return {
                    'status': 'blocked',
                    'blocked_nodes': unresolved,
                    'blocked_inputs': self._flatten_blocked_inputs(unresolved),
                    'upstream_nodes': pending['upstream_nodes'],
                }
        if action == 'run_upstream':
            plan = self._effective_run_plan(self._plan_for_scope(node_id, scope=scope, include_upstream=True))
            pending = self._preflight_plan(plan)
            blocked_nodes = cast(list[dict[str, Any]], pending['blocked_nodes'])
            unresolved = self._blocked_nodes_with_unresolved_inputs(blocked_nodes, allow_stale_only=False)
            if unresolved:
                return {
                    'status': 'blocked',
                    'blocked_nodes': unresolved,
                    'blocked_inputs': self._flatten_blocked_inputs(unresolved),
                    'upstream_nodes': pending['upstream_nodes'],
                }
        if not plan:
            return {'status': 'noop', 'node_id': node_id, 'node_ids': []}
        return self._execute_managed_run(
            plan=plan,
            mode=run_mode,
            target_json={'node_id': node_id, 'plan': plan, 'scope': scope},
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_run is None or self._active_run.run_id != run_id:
                return {'run_id': run_id, 'status': 'not_running'}
            self._active_run.cancel_reason = 'manual'
            self._active_run.cancel_event.set()
        return {'run_id': run_id, 'status': 'cancelling'}

    def start_selection_run(self, node_ids: list[str], *, action: str | None = None) -> dict[str, Any]:
        self.project_service.require_project()
        plan = self._effective_run_plan(self._plan_for_selection(node_ids))
        pending = self._preflight_plan(plan)
        blocked_nodes = cast(list[dict[str, Any]], pending['blocked_nodes'])
        if blocked_nodes and action is None:
            return {'requires_confirmation': True, **pending}
        if action == 'run_upstream':
            plan = self._effective_run_plan(self._plan_for_selection(node_ids, include_upstream=True))
            pending = self._preflight_plan(plan)
            blocked_nodes = cast(list[dict[str, Any]], pending['blocked_nodes'])
            unresolved = self._blocked_nodes_with_unresolved_inputs(blocked_nodes, allow_stale_only=False)
            if unresolved:
                return {
                    'status': 'blocked',
                    'blocked_nodes': unresolved,
                    'blocked_inputs': self._flatten_blocked_inputs(unresolved),
                    'upstream_nodes': pending['upstream_nodes'],
                }
        elif action == 'use_stale':
            unresolved = self._blocked_nodes_with_unresolved_inputs(blocked_nodes, allow_stale_only=True)
            if unresolved:
                return {
                    'status': 'blocked',
                    'blocked_nodes': unresolved,
                    'blocked_inputs': self._flatten_blocked_inputs(unresolved),
                    'upstream_nodes': pending['upstream_nodes'],
                }
        if not plan:
            return {'status': 'noop', 'node_ids': []}
        return self._execute_managed_run(
            plan=plan,
            mode=RunMode.RUN_STALE,
            target_json={'scope': 'selection', 'node_ids': node_ids, 'plan': plan},
        )

    def interrupt_active_run_if_nodes_affected(
        self, changed_node_ids: list[str], graph: GraphData
    ) -> dict[str, Any] | None:
        with self._lock:
            active = self._active_run
            if active is None:
                return None
            affected_nodes = _affected_plan_nodes(active, changed_node_ids, graph)
            if not affected_nodes:
                return None
            active.cancel_reason = 'graph_edit'
            active.cancel_event.set()
        return {
            'run_id': active.run_id,
            'node_id': active.current_node,
            'node_ids': affected_nodes,
        }

    def preflight(self, node_id: str) -> dict[str, Any]:
        node = self.project_service.get_node(node_id)
        if node.kind in {NodeKind.CONSTANT, NodeKind.FILE_INPUT, NodeKind.ORGANIZER, NodeKind.AREA, NodeKind.DASHBOARD}:
            return {'blocked_inputs': [], 'upstream_nodes': [], 'total_nodes': 0}
        blocked_inputs = self._blocked_inputs_for_node(node_id)
        upstream_nodes = upstream_closure(self.project_service.graph(), node_id)
        return {'blocked_inputs': blocked_inputs, 'upstream_nodes': upstream_nodes, 'total_nodes': len(upstream_nodes)}

    def _plan_for_scope(self, node_id: str, *, scope: str, include_upstream: bool = False) -> list[str]:
        graph = self.project_service.graph()
        requested: set[str] = {node_id}
        if scope == 'ancestors':
            requested.update(upstream_closure(graph, node_id))
        elif scope == 'descendants':
            requested.update(downstream_closure(graph, node_id))
        elif scope != 'node':
            raise InvalidRequestError(f'Unknown run scope `{scope}`.')
        if include_upstream:
            requested_with_upstream = set(requested)
            for candidate in requested:
                requested_with_upstream.update(upstream_closure(graph, candidate))
            requested = requested_with_upstream
        ordered = run_plan_for_node(graph, node_id, upstream_node_ids=list(requested - {node_id}))
        return [
            candidate for candidate in ordered if self.project_service.get_node(candidate).kind == NodeKind.NOTEBOOK
        ]

    def _plan_for_selection(self, node_ids: list[str], *, include_upstream: bool = False) -> list[str]:
        graph = self.project_service.graph()
        graph_node_ids = {node.id for node in graph.nodes}
        requested = {node_id for node_id in node_ids if node_id in graph_node_ids}
        if include_upstream:
            requested_with_upstream = set(requested)
            for candidate in requested:
                requested_with_upstream.update(upstream_closure(graph, candidate))
            requested = requested_with_upstream
        ordered = topological_nodes(graph)
        return [
            candidate
            for candidate in ordered
            if candidate in requested and self.project_service.get_node(candidate).kind == NodeKind.NOTEBOOK
        ]

    def _preflight_plan(self, plan: list[str]) -> dict[str, Any]:
        blocked_nodes: list[dict[str, Any]] = []
        upstream_nodes: set[str] = set()
        satisfied_inputs_from: set[str] = set()
        for node_id in plan:
            blocked_inputs = self._blocked_inputs_for_node(node_id, satisfied_inputs_from=satisfied_inputs_from)
            if blocked_inputs:
                blocked_nodes.append({'node_id': node_id, 'blocked_inputs': blocked_inputs})
            upstream_nodes.update(upstream_closure(self.project_service.graph(), node_id))
            satisfied_inputs_from.add(node_id)
        return {
            'blocked_nodes': blocked_nodes,
            'blocked_inputs': self._flatten_blocked_inputs(blocked_nodes),
            'upstream_nodes': sorted(upstream_nodes),
            'total_nodes': len(plan),
        }

    def _effective_run_plan(self, plan: list[str]) -> list[str]:
        effective: list[str] = []
        scheduled: set[str] = set()
        for node_id in plan:
            if self._node_requires_run(node_id, scheduled):
                effective.append(node_id)
                scheduled.add(node_id)
        return effective

    def _node_requires_run(self, node_id: str, scheduled_inputs_from: set[str]) -> bool:
        if self._node_has_nonready_outputs(node_id):
            return True
        interface = self.project_service.latest_interface(node_id)
        for port in interface.get('inputs', []):
            binding = self._binding_for_input(node_id, port['name'])
            if binding is not None and binding['source_node'] in scheduled_inputs_from:
                return True
        return False

    def _blocked_inputs_for_node(
        self,
        node_id: str,
        *,
        satisfied_inputs_from: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        interface = self.project_service.latest_interface(node_id)
        blocked_inputs: list[dict[str, Any]] = []
        for port in interface.get('inputs', []):
            binding = self._binding_for_input(node_id, port['name'])
            if binding is None:
                if port.get('has_default'):
                    continue
                blocked_inputs.append({'name': port['name'], 'state': ArtifactState.PENDING.value, 'source': None})
                continue
            if satisfied_inputs_from is not None and binding['source_node'] in satisfied_inputs_from:
                continue
            head = self.project_service.require_project().state_db.get_artifact_head(
                binding['source_node'], binding['source_port']
            )
            state = ArtifactState.PENDING.value if head is None else str(head['state'])
            if state != ArtifactState.READY.value:
                blocked_inputs.append(
                    {
                        'name': port['name'],
                        'state': state,
                        'source': f'{binding["source_node"]}/{binding["source_port"]}',
                    }
                )
        return blocked_inputs

    def _flatten_blocked_inputs(self, blocked_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for blocked_node in blocked_nodes:
            flattened.extend(cast(list[dict[str, Any]], blocked_node.get('blocked_inputs', [])))
        return flattened

    def _blocked_nodes_with_unresolved_inputs(
        self,
        blocked_nodes: list[dict[str, Any]],
        *,
        allow_stale_only: bool,
    ) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        for blocked_node in blocked_nodes:
            blocked_inputs = cast(list[dict[str, Any]], blocked_node.get('blocked_inputs', []))
            if allow_stale_only:
                remaining = [item for item in blocked_inputs if item['state'] != ArtifactState.STALE.value]
            else:
                remaining = self._unrunnable_inputs(blocked_inputs)
            if remaining:
                unresolved.append({**blocked_node, 'blocked_inputs': remaining})
        return unresolved

    def run_all_stale(self) -> dict[str, Any]:
        graph = self.project_service.graph()
        state_db = self.project_service.require_project().state_db
        nodes = stale_or_pending_nodes(
            graph,
            [*state_db.list_artifact_heads(), *state_db.list_asset_heads()],
            notebook_execution_heads=state_db.list_notebook_execution_heads(),
        )
        if not nodes:
            return {'status': 'noop', 'node_ids': []}
        blocked_nodes: list[dict[str, Any]] = []
        for node_id in nodes:
            pending = self.preflight(node_id)
            blocked_inputs = cast(list[dict[str, Any]], pending['blocked_inputs'])
            unresolved = self._unrunnable_inputs(blocked_inputs)
            if unresolved:
                blocked_nodes.append(
                    {
                        'node_id': node_id,
                        'blocked_inputs': unresolved,
                    }
                )
        if blocked_nodes:
            raise InvalidRequestError(
                'Run queue is blocked by missing required inputs: '
                + json.dumps({'blocked_nodes': blocked_nodes}, sort_keys=True)
            )
        return self._execute_managed_run(
            plan=nodes,
            mode=RunMode.RUN_STALE,
            target_json={'scope': 'project', 'node_ids': nodes},
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        self.publish_pending_session_events()
        return [
            {
                'session_id': session.session_id,
                'node_id': session.node_id,
                'run_id': session.run_id,
                'url': session.url,
                'ready': self.session_manager.is_ready(session.session_id),
            }
            for session in self.session_manager.list()
        ]

    def publish_pending_session_events(self) -> None:
        self.session_manager.list()
        self._publish_stopped_session_events()

    def stop_session(self, session_id: str) -> dict[str, Any]:
        session = self.session_manager.get(session_id)
        if session is None:
            raise NotFoundError(f'Unknown editor session `{session_id}`.')
        result = {'session_id': session_id, 'node_id': session.node_id, 'status': 'stopped'}
        self.session_manager.stop(session_id, record_stop=False)
        with self._lock:
            self._editor_session_output_snapshots.pop(session.node_id, None)
        self._publish_session_event('session.stopped', session=session, payload={'status': 'stopped'})
        return result

    def stop(self) -> None:
        if self._active_run is not None:
            self._active_run.cancel_event.set()
        with self._lock:
            self._orchestrator_node_states = {}
            self._editor_session_output_snapshots = {}
        self.session_manager.stop_all()
        self._publish_stopped_session_events()

    def sync_editor_session_outputs(self, node_id: str) -> None:
        project = self.project_service.require_project()
        current_snapshot = self._output_snapshot_for_node(node_id)
        with self._lock:
            previous_snapshot = self._editor_session_output_snapshots.get(node_id)
            self._editor_session_output_snapshots[node_id] = current_snapshot
        if previous_snapshot is None:
            return
        graph_version = int(self.project_service.graph().meta['graph_version'])
        project_id = project.metadata.project_id

        current_assets = current_snapshot['assets']
        previous_assets = previous_snapshot['assets']
        for asset_name, (current_version_id, current_state) in current_assets.items():
            previous_version_id, previous_state = previous_assets.get(asset_name, (None, ArtifactState.PENDING.value))
            if current_version_id is not None and current_version_id != previous_version_id:
                self.project_service.event_service.publish(
                    'asset.version_created',
                    project_id=project_id,
                    graph_version=graph_version,
                    payload={
                        'node_id': node_id,
                        'asset_name': asset_name,
                        'asset_version_id': current_version_id,
                        'new_state': current_state,
                    },
                )
                continue
            if current_state != previous_state:
                self.project_service.event_service.publish(
                    'asset.state_changed',
                    project_id=project_id,
                    graph_version=graph_version,
                    payload={
                        'node_id': node_id,
                        'asset_name': asset_name,
                        'old_state': previous_state,
                        'new_state': current_state,
                    },
                )

        current_artifacts = current_snapshot['artifacts']
        previous_artifacts = previous_snapshot['artifacts']
        for artifact_name, (_, current_state) in current_artifacts.items():
            _, previous_state = previous_artifacts.get(artifact_name, (None, ArtifactState.PENDING.value))
            if current_state == previous_state:
                continue
            self.project_service.event_service.publish(
                'artifact.state_changed',
                project_id=project_id,
                graph_version=graph_version,
                payload={
                    'node_id': node_id,
                    'artifact_name': artifact_name,
                    'old_state': previous_state,
                    'new_state': current_state,
                },
            )

    def orchestrator_state(self) -> dict[str, dict[str, Any]]:
        project = self.project_service.require_project()
        with self._lock:
            return {
                node_id: {
                    'node_id': state.node_id,
                    'run_id': state.run_id,
                    'status': state.status,
                    'started_at': state.started_at,
                    'completed_at': state.completed_at,
                }
                for node_id, state in self._orchestrator_node_states.items()
                if project.state_db.live_incarnation_id(node_id) == state.incarnation_id
            }

    def _run_single_node(self, run_id: str, node_id: str, active_run: ActiveRun) -> dict[str, Any]:
        project = self.project_service.require_project()
        node = self.project_service.get_node(node_id)
        if node.kind in {NodeKind.CONSTANT, NodeKind.FILE_INPUT, NodeKind.ORGANIZER, NodeKind.AREA, NodeKind.DASHBOARD}:
            return {'status': 'ok', 'outputs': []}
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return {'status': 'error', 'node_id': node_id, 'error': f'Notebook `{node_id}` has no parsed interface.'}
        issues = interface.get('issues', [])
        if any(issue['severity'] == ValidationSeverity.ERROR.value for issue in issues):
            return {'status': 'error', 'node_id': node_id, 'error': f'Notebook `{node_id}` has validation errors.'}
        notebook_path = project.paths.notebook_path(node_id)
        source_hash = compute_source_hash(notebook_path)
        bindings = self._bindings_for_node(node_id)
        outputs = {
            port['name']: {
                'data_type': port['data_type'],
                'role': port['role'],
                'description': port.get('description'),
                'kind': port.get('kind', 'value'),
                'direction': 'output',
            }
            for port in interface.get('outputs', [])
        }
        manifest = RunManifest(
            project_root=str(project.paths.root),
            node_id=node_id,
            notebook_path=str(notebook_path),
            run_id=run_id,
            source_hash=source_hash,
            lineage_mode=LineageMode.MANAGED.value,
            bindings=bindings,
            outputs=outputs,
            assets=self._asset_declarations_for_node(node_id),
        )
        stdout_path, stderr_path = self._prepare_execution_log_files(run_id=run_id, node_id=node_id)
        manifest.stdout_path = str(stdout_path)
        manifest.stderr_path = str(stderr_path)

        def remember_process(process) -> None:
            active_run.process = process

        def record_progress(progress_payload: dict[str, object]) -> None:
            started_at = active_run.current_node_started_at or utc_now_iso()
            total_cells = progress_payload.get('total_cells')
            project.state_db.upsert_orchestrator_execution_meta(
                node_id=node_id,
                run_id=run_id,
                status='running',
                started_at=started_at,
                current_cell=cast(dict[str, Any], progress_payload),
                total_cells=int(total_cells) if isinstance(total_cells, int) else None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            self.project_service.event_service.publish(
                'run.progress',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'run_id': run_id,
                    'node_id': node_id,
                    'started_at': started_at,
                    'current_cell': progress_payload,
                },
            )

        result = self.worker_runner.run(
            manifest,
            temp_dir=project.paths.worker_temp_dir,
            cancel_event=active_run.cancel_event,
            on_process_started=remember_process,
            on_progress=record_progress,
        )
        missing_logs = self._missing_execution_log_streams(stdout_path=stdout_path, stderr_path=stderr_path)
        if missing_logs:
            return {
                'status': 'error',
                'node_id': node_id,
                'error': f'Managed run log file(s) missing for node `{node_id}`: {", ".join(missing_logs)}.',
                'outputs': [],
            }
        if result.get('status') != 'ok':
            result.setdefault('node_id', node_id)
        raw_outputs = result.get('outputs')
        outputs = cast(list[dict[str, Any]], raw_outputs) if isinstance(raw_outputs, list) else []
        for output in outputs:
            self.project_service.event_service.publish(
                'artifact.state_changed',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'node_id': node_id,
                    'artifact_name': output['artifact_name'],
                    'new_state': output['state'],
                },
            )
        raw_assets = result.get('assets')
        assets = cast(list[dict[str, Any]], raw_assets) if isinstance(raw_assets, list) else []
        for asset in assets:
            self.project_service.event_service.publish(
                'asset.version_created',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'node_id': node_id,
                    'asset_name': asset['asset_name'],
                    'asset_version_id': asset['asset_version_id'],
                    'new_state': asset['state'],
                },
            )
        return result

    def _execute_managed_run(self, *, plan: list[str], mode: RunMode, target_json: dict[str, Any]) -> dict[str, Any]:
        launch = self._prepare_managed_run(plan=plan, mode=mode, target_json=target_json)
        self._start_managed_run_thread(launch)
        return {
            'run_id': launch['run_id'],
            'status': 'running',
            'node_ids': list(plan),
        }

    def _prepare_managed_run(self, *, plan: list[str], mode: RunMode, target_json: dict[str, Any]) -> dict[str, Any]:
        project = self.project_service.require_project()
        graph_version = int(self.project_service.graph().meta['graph_version'])
        started_at = utc_now_iso()
        with self._lock:
            if self._active_run is not None:
                raise RunConflictError('Another run is already active for this project.')
            run_id = str(uuid.uuid4())
            active = ActiveRun(run_id=run_id, cancel_event=Event(), node_ids=list(plan))
            self._active_run = active
            self._orchestrator_node_states = {
                node_id: OrchestratorNodeState(
                    node_id=node_id,
                    incarnation_id=project.state_db.live_incarnation_id(node_id) or '',
                    run_id=run_id,
                    status='queued',
                )
                for node_id in plan
            }
        project.state_db.record_run(
            run_id,
            project.metadata.project_id,
            mode.value,
            target_json,
            graph_version,
            {'started_at': started_at},
        )
        self.project_service.event_service.publish(
            'run.queued',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={'run_id': run_id, 'node_ids': plan, 'mode': mode.value},
        )
        project.state_db.update_run_status(run_id, RunStatus.RUNNING)
        self.project_service.event_service.publish(
            'run.started',
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload={'run_id': run_id, 'node_ids': plan, 'mode': mode.value},
        )
        return {
            'project': project,
            'graph_version': graph_version,
            'run_id': run_id,
            'active': active,
            'plan': list(plan),
            'mode': mode,
        }

    def _start_managed_run_thread(self, launch: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._run_managed_plan,
            args=(
                cast(Any, launch['project']),
                int(launch['graph_version']),
                str(launch['run_id']),
                cast(ActiveRun, launch['active']),
                cast(list[str], launch['plan']),
            ),
            daemon=True,
        )
        thread.start()

    def _run_managed_plan(
        self,
        project,
        graph_version: int,
        run_id: str,
        active: ActiveRun,
        plan: list[str],
    ) -> None:
        try:
            for index, current_node_id in enumerate(plan, start=1):
                active.current_node = current_node_id
                active.current_node_started_at = utc_now_iso()
                active.current_node_started_monotonic = time.monotonic()
                self._clear_runtime_notices(current_node_id)
                stdout_path, stderr_path = self._prepare_execution_log_files(run_id=run_id, node_id=current_node_id)
                with self._lock:
                    self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                        node_id=current_node_id,
                        incarnation_id=project.state_db.live_incarnation_id(current_node_id) or '',
                        run_id=run_id,
                        status='running',
                        started_at=active.current_node_started_at,
                    )
                project.state_db.upsert_orchestrator_execution_meta(
                    node_id=current_node_id,
                    run_id=run_id,
                    status='running',
                    started_at=active.current_node_started_at,
                    current_cell=None,
                    stdout_path=str(stdout_path),
                    stderr_path=str(stderr_path),
                )
                self.project_service.event_service.publish(
                    'run.progress',
                    project_id=project.metadata.project_id,
                    graph_version=graph_version,
                    payload={
                        'run_id': run_id,
                        'node_id': current_node_id,
                        'step': index,
                        'total_steps': len(plan),
                        'started_at': active.current_node_started_at,
                    },
                )
                result = self._run_single_node(run_id, current_node_id, active)
                self._record_run_warning_notices(run_id=run_id, node_id=current_node_id, result=result)
                progress = result.get('progress') if isinstance(result.get('progress'), dict) else None
                total_cells = progress.get('total_cells') if isinstance(progress, dict) else None
                current_cell_number = progress.get('cell_number') if isinstance(progress, dict) else None
                if progress is not None:
                    project.state_db.upsert_orchestrator_execution_meta(
                        node_id=current_node_id,
                        run_id=run_id,
                        status='running',
                        started_at=active.current_node_started_at or utc_now_iso(),
                        current_cell=cast(dict[str, Any], progress),
                        total_cells=int(total_cells) if isinstance(total_cells, int) else None,
                        stdout_path=str(stdout_path),
                        stderr_path=str(stderr_path),
                    )
                if result['status'] == 'cancelled':
                    finished_at = utc_now_iso()
                    with self._lock:
                        self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                            node_id=current_node_id,
                            incarnation_id=project.state_db.live_incarnation_id(current_node_id) or '',
                            run_id=run_id,
                            status='cancelled',
                            started_at=active.current_node_started_at,
                            completed_at=finished_at,
                        )
                    project.state_db.upsert_orchestrator_execution_meta(
                        node_id=current_node_id,
                        run_id=run_id,
                        status='cancelled',
                        started_at=active.current_node_started_at or finished_at,
                        ended_at=finished_at,
                        duration_seconds=self._elapsed_seconds(active.current_node_started_monotonic),
                        current_cell=cast(dict[str, Any], progress) if progress is not None else None,
                        total_cells=int(total_cells) if isinstance(total_cells, int) else None,
                        last_completed_cell_number=int(current_cell_number) - 1
                        if isinstance(current_cell_number, int) and current_cell_number > 1
                        else None,
                        stdout_path=str(stdout_path),
                        stderr_path=str(stderr_path),
                    )
                    cancelled_by_graph_edit = active.cancel_reason == 'graph_edit'
                    project.state_db.update_run_status(run_id, RunStatus.CANCELLED)
                    if cancelled_by_graph_edit:
                        self._record_graph_edit_interruption(run_id=run_id, active_run=active)
                    self.project_service.event_service.publish(
                        'run.failed',
                        project_id=project.metadata.project_id,
                        graph_version=graph_version,
                        payload={
                            'run_id': run_id,
                            'status': 'cancelled',
                            'cancelled_by_graph_edit': cancelled_by_graph_edit,
                        },
                    )
                    return
                if result['status'] != 'ok':
                    finished_at = utc_now_iso()
                    with self._lock:
                        self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                            node_id=current_node_id,
                            incarnation_id=project.state_db.live_incarnation_id(current_node_id) or '',
                            run_id=run_id,
                            status='failed',
                            started_at=active.current_node_started_at,
                            completed_at=finished_at,
                        )
                    project.state_db.upsert_orchestrator_execution_meta(
                        node_id=current_node_id,
                        run_id=run_id,
                        status='failed',
                        started_at=active.current_node_started_at or finished_at,
                        ended_at=finished_at,
                        duration_seconds=self._elapsed_seconds(active.current_node_started_monotonic),
                        current_cell=cast(dict[str, Any], progress) if progress is not None else None,
                        total_cells=int(total_cells) if isinstance(total_cells, int) else None,
                        last_completed_cell_number=int(current_cell_number) - 1
                        if isinstance(current_cell_number, int) and current_cell_number > 1
                        else None,
                        stdout_path=str(stdout_path),
                        stderr_path=str(stderr_path),
                        error=str(result.get('traceback') or result.get('error') or 'Run failed.'),
                    )
                    self._record_run_failure_notice(run_id=run_id, result=result)
                    project.state_db.update_run_status(run_id, RunStatus.FAILED, failure_json=result)
                    self.project_service.event_service.publish(
                        'run.failed',
                        project_id=project.metadata.project_id,
                        graph_version=graph_version,
                        payload={'run_id': run_id, 'failure': result},
                    )
                    return
                finished_at = utc_now_iso()
                with self._lock:
                    self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                        node_id=current_node_id,
                        incarnation_id=project.state_db.live_incarnation_id(current_node_id) or '',
                        run_id=run_id,
                        status='succeeded',
                        started_at=active.current_node_started_at,
                        completed_at=finished_at,
                    )
                project.state_db.upsert_orchestrator_execution_meta(
                    node_id=current_node_id,
                    run_id=run_id,
                    status='succeeded',
                    started_at=active.current_node_started_at or finished_at,
                    ended_at=finished_at,
                    duration_seconds=self._elapsed_seconds(active.current_node_started_monotonic),
                    current_cell=None,
                    total_cells=int(total_cells) if isinstance(total_cells, int) else None,
                    last_completed_cell_number=int(total_cells) if isinstance(total_cells, int) else None,
                    stdout_path=str(stdout_path),
                    stderr_path=str(stderr_path),
                )
                self._record_notebook_execution_success(
                    node_id=current_node_id,
                    run_id=run_id,
                    started_at=active.current_node_started_at or finished_at,
                    finished_at=finished_at,
                )
            project.state_db.update_run_status(run_id, RunStatus.SUCCEEDED)
            self.project_service.event_service.publish(
                'run.finished',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'run_id': run_id, 'status': 'succeeded'},
            )
        except Exception as exc:
            failure = {'status': 'error', 'node_id': active.current_node or 'project', 'error': str(exc), 'outputs': []}
            self._record_run_failure_notice(run_id=run_id, result=failure)
            project.state_db.update_run_status(run_id, RunStatus.FAILED, failure_json=failure)
            self.project_service.event_service.publish(
                'run.failed',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'run_id': run_id, 'failure': failure},
            )
        finally:
            with self._lock:
                if self._active_run is not None and self._active_run.run_id == run_id:
                    self._active_run = None
                    self._orchestrator_node_states = {}

    def _record_run_failure_notice(self, *, run_id: str, result: dict[str, Any]) -> None:
        node_id = str(result.get('node_id') or 'project')
        error = str(result.get('error') or 'Run failed.')
        message = error
        if node_id != 'project':
            graph = self.project_service.graph()
            failed_node = next((node for node in graph.nodes if node.id == node_id), None)
            if failed_node is not None:
                message = f'Run failed in {_describe_node_label(failed_node.title, failed_node.id)}. {error}'
        details = {
            'run_id': run_id,
            **result,
        }
        self.project_service.record_notice(
            issue_id=f'run_failed:{node_id}:{run_id}',
            node_id=None if node_id == 'project' else node_id,
            severity=ValidationSeverity.ERROR,
            code='run_failed',
            message=message,
            details=details,
        )

    def _record_run_warning_notices(self, *, run_id: str, node_id: str, result: dict[str, Any]) -> None:
        raw_warnings = result.get('warnings')
        if not isinstance(raw_warnings, list):
            return
        graph = self.project_service.graph()
        node = next((entry for entry in graph.nodes if entry.id == node_id), None)
        node_label = _describe_node_label(node.title, node.id) if node is not None else _format_markdown_code(node_id)
        for index, warning in enumerate(raw_warnings, start=1):
            if not isinstance(warning, dict):
                continue
            warning_message = str(warning.get('message') or 'Notebook emitted a runtime warning.')
            details = {
                'run_id': run_id,
                'node_id': node_id,
                'warning': warning,
                'index': index,
            }
            self.project_service.record_notice(
                issue_id=f'run_warning:{node_id}:{run_id}:{index}',
                node_id=node_id,
                severity=ValidationSeverity.WARNING,
                code='run_warning',
                message=f'Warning in {node_label}. {warning_message}',
                details=details,
            )

    def _clear_runtime_notices(self, node_id: str) -> None:
        self.project_service.require_project().state_db.dismiss_persistent_notices_for_node(
            node_id,
            RUNTIME_NOTICE_CODES,
        )

    def _bindings_for_node(self, node_id: str) -> dict[str, dict[str, Any]]:
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return {}
        bindings = {}
        for port in interface.get('inputs', []):
            binding = self._binding_for_input(node_id, port['name'])
            if binding is None:
                bindings[port['name']] = {
                    'source_node': '',
                    'source_artifact': '',
                    'data_type': port['data_type'],
                    'default': port.get('default'),
                    'has_default': bool(port.get('has_default', False)),
                }
            else:
                bindings[port['name']] = {
                    'source_node': binding['source_node'],
                    'source_artifact': binding['source_port'],
                    'data_type': port['data_type'],
                    'default': port.get('default'),
                    'has_default': bool(port.get('has_default', False)),
                }
        return bindings

    def _binding_for_input(self, node_id: str, input_name: str) -> dict[str, str] | None:
        graph = self.project_service.graph()
        binding = resolve_input_binding(graph, node_id=node_id, input_name=input_name)
        if binding is None:
            return None
        return {'source_node': binding[0], 'source_port': binding[1]}

    def _node_has_nonready_outputs(self, node_id: str) -> bool:
        interface = self.project_service.latest_interface(node_id)
        asset_names = [
            str(declaration['name'])
            for declaration in self.project_service.require_project().state_db.list_asset_declarations(node_id)
        ]
        if interface is None and not asset_names:
            return True
        output_names = [] if interface is None else [str(port['name']) for port in interface.get('outputs', [])]
        state_db = self.project_service.require_project().state_db
        if not output_names and not asset_names:
            if notebook_uses_execution_head(self.project_service, node_id, interface):
                execution_head = state_db.get_notebook_execution_head(node_id)
                if execution_head is None:
                    return True
                return execution_head.get('state') != ArtifactState.READY.value
            return False
        for output_name in output_names:
            head = state_db.get_artifact_head(node_id, output_name)
            if head is None or head['state'] != ArtifactState.READY.value:
                return True
        for asset_name in asset_names:
            head = state_db.get_asset_head(node_id, asset_name)
            if head is None or head['state'] != ArtifactState.READY.value:
                return True
        return False

    def _unrunnable_inputs(self, blocked_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        for blocked in blocked_inputs:
            source = blocked.get('source')
            if source is None:
                unresolved.append(blocked)
                continue
            if blocked['state'] != ArtifactState.PENDING.value:
                continue
            source_node, _, _ = str(source).partition('/')
            try:
                node = self.project_service.get_node(source_node)
            except KeyError:
                unresolved.append(blocked)
                continue
            if node.kind in {NodeKind.CONSTANT, NodeKind.FILE_INPUT}:
                unresolved.append(blocked)
        return unresolved

    def _start_edit_session(self, node_id: str) -> dict[str, Any]:
        project = self.project_service.require_project()
        blockers = self.project_service.frozen_block_blockers_for_node_edit(node_id)
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
        self._clear_runtime_notices(node_id)
        notebook_path = project.paths.notebook_path(node_id)
        source_hash = compute_source_hash(notebook_path)
        run_id = f'edit-{uuid.uuid4()}'
        project.state_db.record_run(
            run_id,
            project.metadata.project_id,
            RunMode.EDIT_RUN.value,
            {'node_id': node_id},
            int(self.project_service.graph().meta['graph_version']),
            {'started_at': utc_now_iso()},
        )
        runtime_env = {
            'BULLETJOURNAL_PROJECT_ROOT': str(project.paths.root),
            'BULLETJOURNAL_NODE_ID': node_id,
            'BULLETJOURNAL_RUN_ID': run_id,
            'BULLETJOURNAL_SOURCE_HASH': source_hash,
            'BULLETJOURNAL_LINEAGE_MODE': LineageMode.INTERACTIVE_HEURISTIC.value,
        }
        session = self.session_manager.create(
            node_id,
            notebook_path,
            run_id=run_id,
            public_base_url=normalize_base_path(getattr(self.server_config, 'base_path', '')),
            runtime_env=runtime_env,
        )
        with self._lock:
            self._editor_session_output_snapshots[node_id] = self._output_snapshot_for_node(node_id)
        self._publish_session_event('session.created', session=session, payload={'ready': False})
        self._watch_session_ready(session.session_id)
        return {
            'mode': RunMode.EDIT_RUN.value,
            'session_id': session.session_id,
            'url': session.url,
            'lineage_mode': LineageMode.INTERACTIVE_HEURISTIC.value,
        }

    def _watch_session_ready(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._session_ready_watchers:
                return
            self._session_ready_watchers.add(session_id)

        thread = threading.Thread(target=self._await_session_ready, args=(session_id,), daemon=True)
        thread.start()

    def _await_session_ready(self, session_id: str) -> None:
        try:
            while True:
                session = self.session_manager.get(session_id)
                if session is None:
                    self._publish_stopped_session_events()
                    return
                if self.session_manager.is_ready(session_id):
                    self._publish_session_event('session.ready', session=session, payload={'ready': True})
                    return
                time.sleep(0.25)
        finally:
            with self._lock:
                self._session_ready_watchers.discard(session_id)

    def _publish_stopped_session_events(self) -> None:
        stopped_session_ids = self.session_manager.consume_stopped_session_ids()
        if not stopped_session_ids:
            return
        project = self.project_service.require_project()
        graph_version = int(self.project_service.graph().meta['graph_version'])
        for session_id in stopped_session_ids:
            self.project_service.event_service.publish(
                'session.stopped',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'session_id': session_id, 'status': 'stopped'},
            )

    def _publish_session_event(self, event_type: str, *, session, payload: dict[str, Any]) -> None:
        project = self.project_service.require_project()
        graph_version = int(self.project_service.graph().meta['graph_version'])
        session_payload = {
            'session_id': session.session_id,
            'node_id': session.node_id,
            'run_id': session.run_id,
            'url': session.url,
            'ready': self.session_manager.is_ready(session.session_id),
            **payload,
        }
        self.project_service.event_service.publish(
            event_type,
            project_id=project.metadata.project_id,
            graph_version=graph_version,
            payload=session_payload,
        )

    def _output_snapshot_for_node(self, node_id: str) -> dict[str, dict[str, tuple[int | None, str]]]:
        state_db = self.project_service.require_project().state_db
        return {
            'assets': {
                str(head['asset_name']): (
                    None if head.get('current_asset_version_id') is None else int(head['current_asset_version_id']),
                    str(head['state']),
                )
                for head in state_db.list_asset_heads(node_id=node_id)
            },
            'artifacts': {
                str(head['artifact_name']): (
                    None if head.get('current_version_id') is None else int(head['current_version_id']),
                    str(head['state']),
                )
                for head in state_db.list_artifact_heads()
                if str(head.get('node_id')) == node_id
            },
        }

    def _record_notebook_execution_success(
        self,
        *,
        node_id: str,
        run_id: str,
        started_at: str,
        finished_at: str,
    ) -> None:
        interface = self.project_service.latest_interface(node_id)
        if not notebook_uses_execution_head(self.project_service, node_id, interface):
            return
        lineage = lineage_metadata_for_notebook(self.project_service, node_id, self.project_service.graph())
        if lineage is None:
            return
        self.project_service.require_project().state_db.upsert_notebook_execution_head(
            node_id=node_id,
            state=ArtifactState.READY,
            source_hash=str(lineage['source_hash']),
            upstream_code_hash=str(lineage['upstream_code_hash']),
            upstream_data_hash=str(lineage['upstream_data_hash']),
            run_id=run_id,
            last_run_started_at=started_at,
            last_run_finished_at=finished_at,
        )

    def _record_graph_edit_interruption(self, *, run_id: str, active_run: ActiveRun) -> None:
        issue_id = build_issue_id(
            node_id='project',
            severity=ValidationSeverity.WARNING,
            code='run_interrupted_by_graph_edit',
            message='An active run was interrupted because the graph changed.',
            details={'run_id': run_id},
        )
        self.project_service.record_notice(
            issue_id=issue_id,
            node_id=None,
            severity=ValidationSeverity.WARNING,
            code='run_interrupted_by_graph_edit',
            message='An active run was interrupted because the graph changed.',
            details={
                'run_id': run_id,
                'current_node': active_run.current_node,
                'node_ids': list(active_run.node_ids),
            },
        )

    def _outputs_for_node(self, node_id: str) -> dict[str, dict[str, Any]]:
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return {}
        return {
            port['name']: {
                'data_type': port['data_type'],
                'role': port['role'],
                'description': port.get('description'),
                'kind': port.get('kind', 'value'),
                'direction': 'output',
            }
            for port in interface.get('outputs', [])
        }

    def _asset_declarations_for_node(self, node_id: str) -> dict[str, dict[str, Any]]:
        declarations = self.project_service.require_project().state_db.list_asset_declarations(node_id)
        return {
            declaration['name']: {
                'node_id': declaration['node_id'],
                'title': declaration['title'],
                'description': declaration.get('description'),
                'declared_asset_type': declaration.get('declared_asset_type'),
                'declaration_index': declaration.get('declaration_index', 0),
            }
            for declaration in declarations
        }

    def _prepare_execution_log_files(self, *, run_id: str, node_id: str) -> tuple[Path, Path]:
        project = self.project_service.require_project()
        stdout_path = project.paths.execution_logs_dir / f'{run_id}_{node_id}.stdout.log'
        stderr_path = project.paths.execution_logs_dir / f'{run_id}_{node_id}.stderr.log'
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        return stdout_path, stderr_path

    @staticmethod
    def _missing_execution_log_streams(*, stdout_path: Path, stderr_path: Path) -> list[str]:
        missing: list[str] = []
        if not stdout_path.exists() or not stdout_path.is_file():
            missing.append('stdout')
        if not stderr_path.exists() or not stderr_path.is_file():
            missing.append('stderr')
        return missing

    def _elapsed_seconds(self, started_monotonic: float | None) -> float | None:
        if started_monotonic is None:
            return None
        return max(time.monotonic() - started_monotonic, 0.0)


def _affected_plan_nodes(active_run: ActiveRun, changed_node_ids: list[str], graph: GraphData) -> list[str]:
    if not changed_node_ids:
        return []
    remaining_nodes = _remaining_plan_nodes(active_run)
    if not remaining_nodes:
        return []
    graph_node_ids = {node.id for node in graph.nodes}
    affected = set(changed_node_ids)
    for node_id in changed_node_ids:
        if node_id in graph_node_ids:
            affected.update(downstream_closure(graph, node_id))
    return [node_id for node_id in remaining_nodes if node_id in affected]


def _remaining_plan_nodes(active_run: ActiveRun) -> list[str]:
    if active_run.current_node is None:
        return list(active_run.node_ids)
    try:
        current_index = active_run.node_ids.index(active_run.current_node)
    except ValueError:
        return list(active_run.node_ids)
    return active_run.node_ids[current_index:]
