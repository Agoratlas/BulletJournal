from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, cast

from bulletjournal.domain.enums import ArtifactState, LineageMode, NodeKind, RunMode, RunStatus, ValidationSeverity
from bulletjournal.domain.errors import RunConflictError
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.planner import run_plan_for_node, stale_or_pending_nodes, upstream_closure
from bulletjournal.execution.runner import WorkerRunner
from bulletjournal.execution.sessions import SessionManager
from bulletjournal.parser.validation import build_issue_id
from bulletjournal.parser.source_hash import compute_source_hash
from bulletjournal.utils import utc_now_iso


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    cancel_event: Event
    node_ids: list[str]
    current_node: str | None = None
    process: object | None = None
    cancel_reason: str | None = None


class RunService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self.worker_runner = WorkerRunner()
        self.session_manager = SessionManager()
        self._lock = Lock()
        self._active_run: ActiveRun | None = None

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
        if self.session_manager.get_by_node(node_id) is not None:
            raise RunConflictError('An Edit & Run session is active for this notebook.')
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

    def interrupt_active_run_for_graph_edit(self) -> dict[str, Any] | None:
        with self._lock:
            active = self._active_run
            if active is None:
                return None
            active.cancel_reason = 'graph_edit'
            active.cancel_event.set()
        return {
            'run_id': active.run_id,
            'node_id': active.current_node,
            'node_ids': list(active.node_ids),
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
            head = self.project_service.require_project().state_db.get_artifact_head(binding['source_node'], binding['source_port'])
            state = ArtifactState.PENDING.value if head is None else str(head['state'])
            if state != ArtifactState.READY.value:
                blocked_inputs.append(
                    {
                        'name': port['name'],
                        'state': state,
                        'source': f"{binding['source_node']}/{binding['source_port']}",
                    }
                )
        upstream_nodes = upstream_closure(self.project_service.graph(), node_id)
        return {'blocked_inputs': blocked_inputs, 'upstream_nodes': upstream_nodes, 'total_nodes': len(upstream_nodes)}

    def run_all_stale(self) -> dict[str, Any]:
        graph = self.project_service.graph()
        nodes = stale_or_pending_nodes(graph, self.project_service.require_project().state_db.list_artifact_heads())
        if not nodes:
            return {'status': 'noop', 'node_ids': []}
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

    def stop(self) -> None:
        if self._active_run is not None:
            self._active_run.cancel_event.set()
        self.session_manager.stop_all()

    def _run_single_node(self, run_id: str, node_id: str, active_run: ActiveRun) -> dict[str, Any]:
        project = self.project_service.require_project()
        node = self.project_service.get_node(node_id)
        if node.kind == NodeKind.FILE_INPUT:
            return {'status': 'ok', 'outputs': []}
        interface = self.project_service.latest_interface(node_id)
        if interface is None:
            return {'status': 'error', 'error': f'Notebook `{node_id}` has no parsed interface.'}
        issues = interface.get('issues', [])
        if any(issue['severity'] == ValidationSeverity.ERROR.value for issue in issues):
            return {'status': 'error', 'error': f'Notebook `{node_id}` has validation errors.'}
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

        def remember_process(process) -> None:
            active_run.process = process

        result = self.worker_runner.run(
            manifest,
            temp_dir=project.paths.uploads_temp_dir,
            cancel_event=active_run.cancel_event,
            on_process_started=remember_process,
        )
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
                self.project_service.event_service.publish(
                    'run.progress',
                    project_id=project.metadata.project_id,
                    graph_version=graph_version,
                    payload={'run_id': run_id, 'node_id': current_node_id, 'step': index, 'total_steps': len(plan)},
                )
                result = self._run_single_node(run_id, current_node_id, active)
                if result['status'] == 'cancelled':
                    cancelled_by_graph_edit = active.cancel_reason == 'graph_edit'
                    project.state_db.update_run_status(run_id, RunStatus.CANCELLED)
                    if cancelled_by_graph_edit:
                        self._record_graph_edit_interruption(run_id=run_id, active_run=active)
                    self.project_service.event_service.publish(
                        'run.failed',
                        project_id=project.metadata.project_id,
                        graph_version=graph_version,
                        payload={'run_id': run_id, 'status': 'cancelled', 'cancelled_by_graph_edit': cancelled_by_graph_edit},
                    )
                    return {'run_id': run_id, 'status': 'cancelled', 'node_results': result}
                if result['status'] != 'ok':
                    project.state_db.update_run_status(run_id, RunStatus.FAILED, failure_json=result)
                    self.project_service.event_service.publish(
                        'run.failed',
                        project_id=project.metadata.project_id,
                        graph_version=graph_version,
                        payload={'run_id': run_id, 'failure': result},
                    )
                    return {'run_id': run_id, 'status': 'failed', 'node_results': result}
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
        output_names = [
            str(port['name'])
            for port in interface.get('outputs', []) + interface.get('assets', [])
        ]
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
        notebook_path = project.paths.notebook_path(node_id)
        source_hash = compute_source_hash(notebook_path)
        bindings = self._bindings_for_node(node_id)
        outputs = self._outputs_for_node(node_id)
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
            'BULLETJOURNAL_BINDINGS_JSON': json.dumps(bindings, sort_keys=True),
            'BULLETJOURNAL_OUTPUTS_JSON': json.dumps(outputs, sort_keys=True),
        }
        session = self.session_manager.create(
            node_id,
            notebook_path,
            run_id=run_id,
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
