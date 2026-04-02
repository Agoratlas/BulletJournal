from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, cast

from bulletjournal.domain.enums import ArtifactState, LineageMode, NodeKind, RunMode, RunStatus, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError, RunConflictError
from bulletjournal.domain.models import GraphData
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.planner import (
    downstream_closure,
    run_plan_for_node,
    stale_or_pending_nodes,
    upstream_closure,
)
from bulletjournal.execution.runner import WorkerRunner
from bulletjournal.execution.sessions import SessionManager
from bulletjournal.parser.validation import build_issue_id
from bulletjournal.parser.source_hash import compute_source_hash
from bulletjournal.config import ServerConfig, normalize_base_path
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
    run_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None


class RunService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self.worker_runner = WorkerRunner()
        self.session_manager = SessionManager()
        self.server_config: ServerConfig | None = None
        self._lock = Lock()
        self._active_run: ActiveRun | None = None
        self._orchestrator_node_states: dict[str, OrchestratorNodeState] = {}

    def has_active_run(self) -> bool:
        with self._lock:
            return self._active_run is not None

    def start_node_run(
        self,
        node_id: str,
        *,
        mode: str,
        action: str | None = None,
    ) -> dict[str, Any]:
        project = self.project_service.require_project()
        run_mode = RunMode(mode)
        if run_mode == RunMode.EDIT_RUN:
            return self._start_edit_session(node_id)
        pending = self.preflight(node_id)
        blocked_inputs = cast(list[dict[str, Any]], pending['blocked_inputs'])
        if blocked_inputs and action is None:
            return {'requires_confirmation': True, **pending}
        if action == 'use_stale':
            unresolved = [item for item in blocked_inputs if item['state'] != ArtifactState.STALE.value]
            if unresolved:
                return {'status': 'blocked', 'blocked_inputs': unresolved, 'upstream_nodes': pending['upstream_nodes']}
        if action == 'run_upstream':
            unresolved = self._unrunnable_inputs(blocked_inputs)
            if unresolved:
                return {'status': 'blocked', 'blocked_inputs': unresolved, 'upstream_nodes': pending['upstream_nodes']}
        if run_mode == RunMode.RUN_STALE and action != 'run_upstream' and not self._node_has_nonready_outputs(node_id):
            return {'status': 'noop', 'node_id': node_id}
        plan = [node_id]
        if action == 'run_upstream':
            graph = self.project_service.graph()
            plan = run_plan_for_node(graph, node_id, upstream_node_ids=upstream_closure(graph, node_id))
        return self._execute_managed_run(
            plan=plan,
            mode=run_mode,
            target_json={'node_id': node_id, 'plan': plan},
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_run is None or self._active_run.run_id != run_id:
                return {'run_id': run_id, 'status': 'not_running'}
            self._active_run.cancel_reason = 'manual'
            self._active_run.cancel_event.set()
        return {'run_id': run_id, 'status': 'cancelling'}

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
        if node.kind == NodeKind.FILE_INPUT:
            return {'blocked_inputs': [], 'upstream_nodes': [], 'total_nodes': 0}
        interface = self.project_service.latest_interface(node_id)
        blocked_inputs = []
        for port in interface.get('inputs', []):
            binding = self._binding_for_input(node_id, port['name'])
            if binding is None:
                if port.get('has_default'):
                    continue
                blocked_inputs.append({'name': port['name'], 'state': ArtifactState.PENDING.value, 'source': None})
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
        upstream_nodes = upstream_closure(self.project_service.graph(), node_id)
        return {'blocked_inputs': blocked_inputs, 'upstream_nodes': upstream_nodes, 'total_nodes': len(upstream_nodes)}

    def run_all_stale(self) -> dict[str, Any]:
        graph = self.project_service.graph()
        nodes = stale_or_pending_nodes(graph, self.project_service.require_project().state_db.list_artifact_heads())
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

    def stop_session(self, session_id: str) -> dict[str, Any]:
        session = self.session_manager.get(session_id)
        if session is None:
            raise NotFoundError(f'Unknown editor session `{session_id}`.')
        self.session_manager.stop(session_id)
        return {'session_id': session_id, 'node_id': session.node_id, 'status': 'stopped'}

    def stop(self) -> None:
        if self._active_run is not None:
            self._active_run.cancel_event.set()
        with self._lock:
            self._orchestrator_node_states = {}
        self.session_manager.stop_all()

    def orchestrator_state(self) -> dict[str, dict[str, Any]]:
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
            }

    def _run_single_node(self, run_id: str, node_id: str, active_run: ActiveRun) -> dict[str, Any]:
        project = self.project_service.require_project()
        node = self.project_service.get_node(node_id)
        if node.kind == NodeKind.FILE_INPUT:
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
            for port in interface.get('outputs', []) + interface.get('assets', [])
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
        return result

    def _execute_managed_run(self, *, plan: list[str], mode: RunMode, target_json: dict[str, Any]) -> dict[str, Any]:
        project = self.project_service.require_project()
        with self._lock:
            if self._active_run is not None:
                raise RunConflictError('Another run is already active for this project.')
            run_id = str(uuid.uuid4())
            active = ActiveRun(run_id=run_id, cancel_event=Event(), node_ids=plan)
            self._active_run = active
            self._orchestrator_node_states = {
                node_id: OrchestratorNodeState(node_id=node_id, run_id=run_id, status='queued') for node_id in plan
            }
        try:
            graph_version = int(self.project_service.graph().meta['graph_version'])
            project.state_db.record_run(
                run_id,
                project.metadata.project_id,
                mode.value,
                target_json,
                graph_version,
                {'started_at': utc_now_iso()},
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
            for index, current_node_id in enumerate(plan, start=1):
                active.current_node = current_node_id
                active.current_node_started_at = utc_now_iso()
                active.current_node_started_monotonic = time.monotonic()
                stdout_path, stderr_path = self._prepare_execution_log_files(run_id=run_id, node_id=current_node_id)
                with self._lock:
                    self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                        node_id=current_node_id,
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
                    return {'run_id': run_id, 'status': 'cancelled', 'node_results': result}
                if result['status'] != 'ok':
                    finished_at = utc_now_iso()
                    with self._lock:
                        self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                            node_id=current_node_id,
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
                    )
                    self._record_run_failure_notice(run_id=run_id, result=result)
                    project.state_db.update_run_status(run_id, RunStatus.FAILED, failure_json=result)
                    self.project_service.event_service.publish(
                        'run.failed',
                        project_id=project.metadata.project_id,
                        graph_version=graph_version,
                        payload={'run_id': run_id, 'failure': result},
                    )
                    return {'run_id': run_id, 'status': 'failed', 'node_results': result}
                finished_at = utc_now_iso()
                with self._lock:
                    self._orchestrator_node_states[current_node_id] = OrchestratorNodeState(
                        node_id=current_node_id,
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
            project.state_db.update_run_status(run_id, RunStatus.SUCCEEDED)
            self.project_service.event_service.publish(
                'run.finished',
                project_id=project.metadata.project_id,
                graph_version=graph_version,
                payload={'run_id': run_id, 'status': 'succeeded'},
            )
            return {'run_id': run_id, 'status': 'succeeded', 'node_ids': plan}
        finally:
            with self._lock:
                self._active_run = None
                self._orchestrator_node_states = {}

    def _record_run_failure_notice(self, *, run_id: str, result: dict[str, Any]) -> None:
        node_id = str(result.get('node_id') or 'project')
        error = str(result.get('error') or 'Run failed.')
        details = {
            'run_id': run_id,
            **result,
        }
        issue_id = build_issue_id(
            node_id=node_id,
            severity=ValidationSeverity.ERROR,
            code='run_failed',
            message=error,
            details=details,
        )
        self.project_service.record_notice(
            issue_id=issue_id,
            node_id=None if node_id == 'project' else node_id,
            severity=ValidationSeverity.ERROR,
            code='run_failed',
            message=error,
            details=details,
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
        for edge in graph.edges:
            if edge.target_node == node_id and edge.target_port == input_name:
                return {'source_node': edge.source_node, 'source_port': edge.source_port}
        return None

    def _node_has_nonready_outputs(self, node_id: str) -> bool:
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return True
        output_names = [str(port['name']) for port in interface.get('outputs', []) + interface.get('assets', [])]
        if not output_names:
            return False
        state_db = self.project_service.require_project().state_db
        for output_name in output_names:
            head = state_db.get_artifact_head(node_id, output_name)
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
            if node.kind == NodeKind.FILE_INPUT:
                unresolved.append(blocked)
        return unresolved

    def _start_edit_session(self, node_id: str) -> dict[str, Any]:
        project = self.project_service.require_project()
        blockers = self.project_service.frozen_notebook_blockers_for_node_edit(node_id)
        if blockers:
            raise InvalidRequestError(self.project_service.freeze_block_message(blockers))
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
        return {
            'mode': RunMode.EDIT_RUN.value,
            'session_id': session.session_id,
            'url': session.url,
            'lineage_mode': LineageMode.INTERACTIVE_HEURISTIC.value,
        }

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
            for port in interface.get('outputs', []) + interface.get('assets', [])
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
