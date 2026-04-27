from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, RunStatus, ValidationSeverity
from bulletjournal.domain.models import ValidationIssue
from bulletjournal.parser.validation import build_issue_id
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB, _database_journal_mode


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

    db.record_run(
        'run-1',
        'project-1',
        'run_stale',
        {'node_id': 'node_a', 'node_ids': ['node_a'], 'plan': ['node_a']},
        1,
        {'started_at': '2026-03-26T00:00:00Z'},
    )
    db.update_run_status('run-1', RunStatus.FAILED, failure_json={'node_id': 'node_a', 'error': 'boom'})
    db.record_run_input('run-1', 'node_a/output', 'hash-1', ArtifactState.READY.value)
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
    )

    assert 'node_a' in db.list_state_node_ids()
    assert db.latest_interface_json('node_a') is not None
    assert any(issue['node_id'] == 'node_a' for issue in db.list_validation_issues())
    assert any(head['node_id'] == 'node_a' for head in db.list_artifact_heads())
    assert 'node_a' in db.list_orchestrator_execution_meta()
    assert any(run['run_id'] == 'run-1' for run in db.list_run_records())
    with db._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM run_inputs WHERE run_id = ?', ('run-1',)).fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM run_outputs WHERE run_id = ?', ('run-1',)).fetchone()[0] == 1

    db.delete_node_state('node_a')

    assert db.latest_interface_json('node_a') is None
    assert all(issue['node_id'] != 'node_a' for issue in db.list_validation_issues())
    assert all(head['node_id'] != 'node_a' for head in db.list_artifact_heads())
    assert 'node_a' not in db.list_orchestrator_execution_meta()
    assert 'node_a' not in db.list_state_node_ids()
    assert all(run['run_id'] != 'run-1' for run in db.list_run_records())
    with db._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM run_inputs WHERE run_id = ?', ('run-1',)).fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM run_outputs WHERE run_id = ?', ('run-1',)).fetchone()[0] == 0


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
    stdout_log = paths.execution_logs_dir / 'run-1_node_a.stdout.log'
    stderr_log = paths.execution_logs_dir / 'run-1_node_a.stderr.log'
    stdout_log.write_text('hello stdout\n', encoding='utf-8')
    stderr_log.write_text('warning on stderr\n', encoding='utf-8')

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
        stdout_path=str(stdout_log),
        stderr_path=str(stderr_log),
    )

    records = db.list_orchestrator_execution_meta()

    assert records['node_a']['stdout'] == {'text': 'hello stdout\n', 'truncated': False, 'size_bytes': 13}
    assert records['node_a']['stderr'] == {'text': 'warning on stderr\n', 'truncated': False, 'size_bytes': 18}


def test_state_db_truncates_execution_log_previews(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project')
    db = StateDB(paths.state_db_path)
    stdout_log = paths.execution_logs_dir / 'run-1_node_a.stdout.log'
    long_log = ''.join(f'line {index}: ' + ('x' * 120) + '\n' for index in range(120))
    stdout_log.write_text(long_log, encoding='utf-8')

    db.upsert_orchestrator_execution_meta(
        node_id='node_a',
        run_id='run-1',
        status='succeeded',
        started_at='2026-03-26T00:00:00Z',
        stdout_path=str(stdout_log),
    )

    records = db.list_orchestrator_execution_meta()

    assert records['node_a']['stdout'] is not None
    assert records['node_a']['stdout']['truncated'] is True
    assert 'line 79' in records['node_a']['stdout']['text']
    assert 'line 0' not in records['node_a']['stdout']['text']
    assert records['node_a']['stdout']['size_bytes'] == len(long_log.encode('utf-8'))


def test_database_journal_mode_defaults_to_delete_for_project_mounts_in_container() -> None:
    mode = _database_journal_mode(Path('/project/metadata/state.db'), in_container=True)

    assert mode == 'DELETE'


def test_database_journal_mode_keeps_wal_outside_container_mounts() -> None:
    mode = _database_journal_mode(Path('/tmp/state.db'), in_container=True)

    assert mode == 'WAL'


def test_database_journal_mode_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv('BULLETJOURNAL_DB_JOURNAL_MODE', 'memory')

    mode = _database_journal_mode(Path('/project/metadata/state.db'), in_container=True)

    assert mode == 'MEMORY'
