from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, ValidationSeverity
from bulletjournal.domain.models import ValidationIssue
from bulletjournal.parser.validation import build_issue_id
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB


def test_state_db_tracks_artifact_head_lifecycle_and_cache_nondeterminism(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    db.ensure_artifact_head('node_a', 'output', ArtifactState.PENDING)
    pending = db.get_artifact_head('node_a', 'output')

    assert pending is not None
    assert pending['current_version_id'] is None
    assert pending['state'] == ArtifactState.PENDING.value

    db.upsert_artifact_object(
        'hash-1', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '1', 'truncated': False}
    )
    db.upsert_artifact_object(
        'hash-2', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '2', 'truncated': False}
    )
    first_version = db.create_artifact_version(
        node_id='node_a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-1',
        source_hash='source-a',
        upstream_code_hash='code-hash',
        upstream_data_hash='data-hash',
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    ready = db.get_artifact_head('node_a', 'output')

    assert first_version > 0
    assert ready is not None
    assert ready['current_version_id'] == first_version
    assert ready['state'] == ArtifactState.READY.value
    assert ready['artifact_hash'] == 'hash-1'

    cache_hit = db.get_cache_hit('node_a', 'output', 'data-hash')
    assert cache_hit == {'artifact_hash': 'hash-1', 'is_nondeterministic': False}

    second_version = db.create_artifact_version(
        node_id='node_a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-2',
        source_hash='source-a',
        upstream_code_hash='code-hash',
        upstream_data_hash='data-hash',
        run_id='run-2',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    cache_hit = db.get_cache_hit('node_a', 'output', 'data-hash')

    assert second_version > first_version
    assert cache_hit == {'artifact_hash': 'hash-2', 'is_nondeterministic': True}

    db.set_artifact_head_state('node_a', 'output', ArtifactState.STALE)
    stale = db.get_artifact_head('node_a', 'output')

    assert stale is not None
    assert stale['state'] == ArtifactState.STALE.value


def test_state_db_can_delete_single_artifact_state(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    db.upsert_artifact_object(
        'hash-1', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '1', 'truncated': False}
    )
    db.upsert_artifact_object(
        'hash-2', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '2', 'truncated': False}
    )
    db.create_artifact_version(
        node_id='node_a',
        artifact_name='keep',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-1',
        source_hash='source-a',
        upstream_code_hash='code-a',
        upstream_data_hash='data-a',
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    db.create_artifact_version(
        node_id='node_a',
        artifact_name='drop',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-2',
        source_hash='source-a',
        upstream_code_hash='code-b',
        upstream_data_hash='data-b',
        run_id='run-2',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    db.delete_artifact_state('node_a', 'drop')

    heads = db.list_artifact_heads()
    assert any(head['artifact_name'] == 'keep' for head in heads)
    assert all(head['artifact_name'] != 'drop' for head in heads)


def test_state_db_delete_node_state_removes_all_visible_node_records(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    db.save_notebook_revision(
        'node_a',
        'source-a',
        'docs',
        {'node_id': 'node_a', 'source_hash': 'source-a', 'inputs': [], 'outputs': [], 'assets': [], 'issues': []},
    )
    db.replace_validation_issues(
        'node_a',
        [
            ValidationIssue(
                issue_id='issue-1',
                node_id='node_a',
                severity=ValidationSeverity.ERROR,
                code='bad',
                message='broken',
            )
        ],
    )
    db.upsert_artifact_object(
        'hash-1', 'json', 'int', 2, None, None, {'kind': 'simple', 'repr': '1', 'truncated': False}
    )
    db.create_artifact_version(
        node_id='node_a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash='hash-1',
        source_hash='source-a',
        upstream_code_hash='code-hash',
        upstream_data_hash='data-hash',
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    assert 'node_a' in db.list_state_node_ids()
    assert db.latest_interface_json('node_a') is not None
    assert any(issue['node_id'] == 'node_a' for issue in db.list_validation_issues())
    assert any(head['node_id'] == 'node_a' for head in db.list_artifact_heads())

    db.delete_node_state('node_a')

    assert db.latest_interface_json('node_a') is None
    assert all(issue['node_id'] != 'node_a' for issue in db.list_validation_issues())
    assert all(head['node_id'] != 'node_a' for head in db.list_artifact_heads())
    assert 'node_a' not in db.list_state_node_ids()


def test_state_db_hides_dismissed_warning_but_keeps_active_errors(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    warning = ValidationIssue(
        issue_id=build_issue_id(
            node_id='node_a',
            severity=ValidationSeverity.WARNING,
            code='warning_code',
            message='Heads up',
        ),
        node_id='node_a',
        severity=ValidationSeverity.WARNING,
        code='warning_code',
        message='Heads up',
    )
    error = ValidationIssue(
        issue_id=build_issue_id(
            node_id='node_a',
            severity=ValidationSeverity.ERROR,
            code='error_code',
            message='Broken',
        ),
        node_id='node_a',
        severity=ValidationSeverity.ERROR,
        code='error_code',
        message='Broken',
    )

    db.replace_validation_issues('node_a', [warning, error])
    db.dismiss_validation_issue(warning.issue_id)

    visible = db.list_validation_issues()
    all_issues = db.list_validation_issues(include_dismissed=True)

    assert [issue['issue_id'] for issue in visible] == [error.issue_id]
    assert {issue['issue_id'] for issue in all_issues} == {warning.issue_id, error.issue_id}


def test_state_db_preserves_persistent_notice_dismissal_across_updates(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    issue_id = build_issue_id(
        node_id='project',
        severity=ValidationSeverity.WARNING,
        code='run_interrupted_by_graph_edit',
        message='An active run was interrupted because the graph changed.',
        details={'run_id': 'run-1'},
    )

    db.save_persistent_notice(
        issue_id=issue_id,
        node_id=None,
        severity=ValidationSeverity.WARNING,
        code='run_interrupted_by_graph_edit',
        message='An active run was interrupted because the graph changed.',
        details={'run_id': 'run-1'},
    )
    db.dismiss_persistent_notice(issue_id)

    assert db.list_persistent_notices() == []

    db.save_persistent_notice(
        issue_id=issue_id,
        node_id=None,
        severity=ValidationSeverity.WARNING,
        code='run_interrupted_by_graph_edit',
        message='An active run was interrupted because the graph changed.',
        details={'run_id': 'run-1', 'current_node': 'sample'},
    )

    assert db.list_persistent_notices() == []
    persisted = db.get_persistent_notice(issue_id)
    assert persisted is not None
    assert persisted['details']['current_node'] == 'sample'


def test_state_db_persists_execution_logs(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)

    db.upsert_orchestrator_execution_meta(
        node_id='node_a',
        run_id='run-1',
        status='succeeded',
        started_at='2026-03-26T00:00:00Z',
        ended_at='2026-03-26T00:00:05Z',
        duration_seconds=5.0,
        current_cell=None,
        total_cells=3,
        last_completed_cell_number=3,
        stdout='hello stdout\n',
        stderr='warning on stderr\n',
    )

    records = db.list_orchestrator_execution_meta()

    assert records['node_a']['stdout'] == 'hello stdout\n'
    assert records['node_a']['stderr'] == 'warning on stderr\n'
