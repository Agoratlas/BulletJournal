import json
from types import SimpleNamespace

import pytest

from bulletjournal.domain.enums import ArtifactState, NodeKind, ValidationSeverity
from bulletjournal.domain.errors import InvalidRequestError
from bulletjournal.domain.models import Edge, GraphData, Node, ValidationIssue
from bulletjournal.execution import worker_main
from bulletjournal.execution.planner import downstream_closure, upstream_closure
from bulletjournal.runtime import artifacts
from bulletjournal.services.artifact_service import ArtifactService, _resolve_constant_value
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


def test_organizer_dependency_closures_keep_lanes_separate():
    graph = GraphData(
        meta={'graph_version': 1},
        nodes=[Node(id=name, title=name, kind=NodeKind.NOTEBOOK) for name in ('nb1', 'nb2', 'nb3', 'nb4')]
        + [Node(id='organizer', title='Organizer', kind=NodeKind.ORGANIZER)],
        edges=[
            Edge(id=str(index), source_node=source, source_port=port, target_node=target, target_port=port)
            for index, (source, target, port) in enumerate(
                [
                    ('nb1', 'organizer', 'A'),
                    ('organizer', 'nb2', 'A'),
                    ('nb3', 'organizer', 'B'),
                    ('organizer', 'nb4', 'B'),
                ]
            )
        ],
        layout=[],
    )
    assert set(upstream_closure(graph, 'nb4')) == {'nb3', 'organizer'}
    assert set(downstream_closure(graph, 'nb1')) == {'organizer', 'nb2'}
    assert set(upstream_closure(graph, 'organizer')) == {'nb1', 'nb3'}
    assert set(downstream_closure(graph, 'organizer')) == {'nb2', 'nb4'}


@pytest.mark.parametrize('text', ['{"a": 1, "a": 2}', '{"nested": {"a": 1, "\\u0061": 2}}'])
def test_constant_json_rejects_duplicate_keys(text):
    with pytest.raises(InvalidRequestError, match=r'duplicate.*key'):
        _resolve_constant_value(data_type='dict', value=None, value_json=text)


def _project(tmp_path):
    service = ProjectService(SimpleNamespace(publish=lambda *args, **kwargs: None), TemplateService())
    service.open_project(init_project_root(tmp_path / 'project').root)
    return service


@pytest.mark.parametrize('kind', ['dashboard', 'area', 'organizer'])
def test_output_state_change_skips_non_output_blocks(tmp_path, kind):
    project = _project(tmp_path)
    try:
        GraphService(project).apply_operations(
            int(project.graph().meta['graph_version']),
            [{'type': f'add_{kind}_node', 'node_id': 'block'}],
        )
        result = ArtifactService(project).set_node_output_states('block', state=ArtifactState.STALE)
        assert result['artifact_names'] == []
        assert result['asset_names'] == []
    finally:
        project.watcher.stop()


def test_deleting_block_removes_notices_even_after_restore(tmp_path):
    project = _project(tmp_path)
    try:
        graph = GraphService(project)
        graph.apply_operations(
            int(project.graph().meta['graph_version']),
            [
                {'type': 'add_constant_node', 'node_id': 'block', 'data_type': 'int'},
            ],
        )
        db = project.require_project().state_db
        db.save_persistent_notice(
            issue_id='warning',
            node_id='block',
            severity=ValidationSeverity.WARNING,
            code='run_warning',
            message='warning',
            details={},
        )
        db.replace_validation_issues(
            'block',
            [
                ValidationIssue(
                    issue_id='error',
                    node_id='block',
                    severity=ValidationSeverity.ERROR,
                    code='error',
                    message='error',
                )
            ],
        )
        deleted = graph.apply_operations(
            int(project.graph().meta['graph_version']),
            [
                {'type': 'delete_node', 'node_id': 'block'},
            ],
        )
        assert db.get_persistent_notice('warning') is None
        graph.restore_tombstone(deleted['tombstone_mutations'][0]['tombstone_id'])
        assert all(issue['issue_id'] != 'error' for issue in db.list_validation_issues())
        assert db.get_persistent_notice('warning') is None
    finally:
        project.watcher.stop()


def test_worker_preserves_push_before_immediate_failure(tmp_path, monkeypatch):
    project = _project(tmp_path)
    try:
        GraphService(project).apply_operations(
            int(project.graph().meta['graph_version']),
            [
                {'type': 'add_notebook_node', 'node_id': 'producer', 'title': 'Producer'},
            ],
        )
        interface = project.latest_interface('producer')
        root = project.require_project().paths.root
        result_path = tmp_path / 'result.json'
        manifest_path = tmp_path / 'manifest.json'
        manifest_path.write_text(
            json.dumps(
                {
                    'project_root': str(root),
                    'node_id': 'producer',
                    'notebook_path': str(root / 'notebooks' / 'producer.py'),
                    'run_id': 'run-push-fail',
                    'source_hash': interface['source_hash'],
                    'lineage_mode': 'managed',
                    'bindings': {},
                    'outputs': {'result': {'data_type': 'int', 'role': 'output'}},
                    'result_path': str(result_path),
                }
            )
        )

        def execute(*args, **kwargs):
            artifacts.push(42, name='result', data_type=int)
            raise RuntimeError('failure after push')

        monkeypatch.setattr(worker_main, 'execute_notebook', execute)
        monkeypatch.setattr(worker_main, '_install_script_runner_progress_hooks', lambda **kwargs: None)
        assert worker_main.main([str(manifest_path)]) == 1
        assert json.loads(result_path.read_text())['error'] == 'failure after push'
        head = project.require_project().state_db.get_artifact_head('producer', 'result')
        assert head is not None and head['current_version_id'] is not None
        assert project.require_project().object_store.load_value(head['artifact_hash'], 'int') == 42
    finally:
        project.watcher.stop()
