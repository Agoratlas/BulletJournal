import os
from datetime import UTC, datetime, timedelta

import pytest

from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.storage.object_gc import ObjectGarbageCollector, ObjectGCSettings
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import init_project_root
from bulletjournal.storage.state_db import StateDB

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _persist(paths, value: str) -> tuple[StateDB, ObjectStore, dict]:
    db = StateDB(paths.state_db_path)
    store = ObjectStore(paths)
    persisted = store.persist_value(value, 'str')
    db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted['extension'],
        persisted['mime_type'],
        persisted['preview'],
    )
    with db._connection() as connection:
        connection.execute(
            'UPDATE objects SET created_at = ?, unreferenced_at = ? WHERE artifact_hash = ?',
            ('2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', persisted['artifact_hash']),
        )
        connection.commit()
    return db, store, persisted


def _gc(paths, **overrides) -> ObjectGarbageCollector:
    values = {'object_retention_seconds': 0, 'batch_size': 10, 'max_batch_bytes': 1024 * 1024}
    values.update(overrides)
    settings = ObjectGCSettings(**values)
    return ObjectGarbageCollector(paths, settings)


def test_gc_dry_run_reports_without_mutation(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, persisted = _persist(paths, 'unused')

    report = _gc(paths).collect(dry_run=True, now=NOW)

    assert report.objects_examined == 1
    assert report.objects_eligible == 1
    assert store.object_path(persisted['artifact_hash']).is_file()
    assert db.get_object_record(persisted['artifact_hash'])['gc_state'] == 'active'


def test_gc_never_collects_artifact_or_shared_asset_references(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, persisted = _persist(paths, 'shared')
    artifact_hash = persisted['artifact_hash']
    db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=artifact_hash,
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='data',
        run_id='run-a',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    db.create_asset_version(
        node_id='node-b',
        asset_name='chart',
        asset_type='chart',
        interactive=False,
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='data',
        run_id='run-b',
        lineage_mode=LineageMode.MANAGED,
        definition={},
        modifier_schema=[],
        default_modifiers={},
        override_schema_hash='schema',
        warnings=[],
        objects=[
            {'object_role': 'dataset', 'object_index': 0, 'artifact_hash': artifact_hash},
            {'object_role': 'thumbnail', 'object_index': 0, 'artifact_hash': artifact_hash},
        ],
    )

    report = _gc(paths).collect(dry_run=False, now=NOW)

    assert report.artifact_roots == 1
    assert report.asset_roots == 1
    assert report.objects_deleted == 0
    assert store.object_path(artifact_hash).is_file()


def test_pin_and_unexpired_lease_are_roots_but_expired_lease_is_not(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, _, pinned = _persist(paths, 'pinned')
    _, _, leased = _persist(paths, 'leased')
    _, _, expired = _persist(paths, 'expired')
    db.pin_object('publication', 'pub-1', pinned['artifact_hash'])
    db.acquire_object_lease(leased['artifact_hash'], 'download', 'request-1', expires_at='2026-08-06T13:00:00Z')
    db.acquire_object_lease(expired['artifact_hash'], 'download', 'request-2', expires_at='2026-08-06T11:00:00Z')

    report = _gc(paths).collect(dry_run=False, now=NOW)

    assert report.pin_roots == 1
    assert report.lease_roots == 1
    assert db.get_object_record(pinned['artifact_hash'])['gc_state'] == 'active'
    assert db.get_object_record(leased['artifact_hash'])['gc_state'] == 'active'
    assert db.get_object_record(expired['artifact_hash']) is None


def test_gc_retention_starts_when_final_reference_disappears(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, persisted = _persist(paths, 'retain-me')
    artifact_hash = persisted['artifact_hash']
    db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=artifact_hash,
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='data',
        run_id='run-a',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    with db._connection() as connection:
        connection.execute('DELETE FROM artifact_heads')
        connection.execute('DELETE FROM artifact_versions')
        recorded = connection.execute(
            'SELECT unreferenced_at FROM objects WHERE artifact_hash = ?', (artifact_hash,)
        ).fetchone()
        assert recorded['unreferenced_at'] is not None
        connection.execute('UPDATE objects SET unreferenced_at = NULL WHERE artifact_hash = ?', (artifact_hash,))
        connection.commit()
    gc = _gc(paths, object_retention_seconds=3600)

    first = gc.collect(dry_run=False, now=NOW)
    before_expiry = gc.collect(dry_run=False, now=NOW + timedelta(seconds=3599))
    expired = gc.collect(dry_run=False, now=NOW + timedelta(seconds=3601))

    assert first.objects_deleted == 0
    assert before_expiry.objects_deleted == 0
    assert expired.objects_deleted == 1
    assert expired.bytes_reclaimed > 0
    assert not store.object_path(artifact_hash).exists()
    assert db.get_object_record(artifact_hash) is None


def test_gc_settings_reject_invalid_values_and_parse_project_meta(tmp_path) -> None:
    with pytest.raises(ValueError):
        ObjectGCSettings(batch_size=0)
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db = StateDB(paths.state_db_path)
    db.set_project_meta('gc_batch_size', '7')
    db.set_project_meta('gc_min_interval_seconds', '120')

    settings = ObjectGCSettings.from_project_meta(db)

    assert settings.batch_size == 7
    assert settings.min_interval_seconds == 120


def test_historical_version_is_pruned_and_run_provenance_survives(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, old = _persist(paths, 'old')
    _, _, current = _persist(paths, 'current')
    old_version = db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=old['artifact_hash'],
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='old',
        run_id='run-old',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    current_version = db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=current['artifact_hash'],
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='current',
        run_id='run-current',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    with db._connection() as connection:
        connection.execute('UPDATE artifact_versions SET created_at = ?', ('2026-08-01T00:00:00Z',))
        connection.commit()

    report = _gc(paths, version_retention_seconds=0).collect(dry_run=False, now=NOW)

    assert report.artifact_versions_pruned == 1
    with db._connection() as connection:
        assert (
            connection.execute('SELECT 1 FROM artifact_versions WHERE version_id = ?', (old_version,)).fetchone()
            is None
        )
        provenance = connection.execute(
            "SELECT version_id, artifact_hash, artifact_role FROM run_outputs WHERE run_id = 'run-old'"
        ).fetchone()
    assert provenance['version_id'] is None
    assert provenance['artifact_hash'] == old['artifact_hash']
    assert provenance['artifact_role'] == ArtifactRole.OUTPUT.value
    assert db.get_artifact_head('node-a', 'output')['current_version_id'] == current_version
    assert store.object_path(current['artifact_hash']).is_file()


def test_shared_object_survives_while_a_current_head_references_it(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, shared = _persist(paths, 'shared-current')
    first = db.create_artifact_version(
        node_id='node-a',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=shared['artifact_hash'],
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='one',
        run_id='run-one',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    second = db.create_artifact_version(
        node_id='node-b',
        artifact_name='output',
        role=ArtifactRole.OUTPUT,
        artifact_hash=shared['artifact_hash'],
        source_hash='source',
        upstream_code_hash='code',
        upstream_data_hash='two',
        run_id='run-two',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    db.delete_artifact_head('node-a', 'output')
    with db._connection() as connection:
        connection.execute('UPDATE artifact_versions SET created_at = ?', ('2026-08-01T00:00:00Z',))
        connection.commit()

    report = _gc(paths, version_retention_seconds=0).collect(dry_run=False, now=NOW)

    assert report.artifact_versions_pruned == 1
    assert db.get_artifact_head('node-b', 'output')['current_version_id'] == second
    assert store.object_path(shared['artifact_hash']).is_file()
    with db._connection() as connection:
        assert connection.execute('SELECT 1 FROM artifact_versions WHERE version_id = ?', (first,)).fetchone() is None


def test_temp_cleanup_is_bounded_and_excludes_logs_and_checkpoints(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    old_timestamp = (NOW - timedelta(days=2)).timestamp()
    files = []
    for directory, name in (
        (paths.uploads_dir, 'upload.tmp'),
        (paths.pulled_files_dir, 'pull.tmp'),
        (paths.worker_temp_dir, 'worker.tmp'),
        (paths.execution_logs_dir, 'run.log'),
        (paths.checkpoints_dir, 'checkpoint.data'),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text('old', encoding='utf-8')
        os.utime(path, (old_timestamp, old_timestamp))
        files.append(path)

    report = _gc(paths, temp_retention_seconds=1).collect(dry_run=False, now=NOW)

    assert report.temp_files_deleted == 3
    assert all(not path.exists() for path in files[:3])
    assert files[3].is_file()
    assert files[4].is_file()


def test_active_work_defers_gc_without_mutation(tmp_path) -> None:
    paths = init_project_root(tmp_path / 'project', initialize_environment=False)
    db, store, persisted = _persist(paths, 'deferred')
    gc = ObjectGarbageCollector(
        paths,
        ObjectGCSettings(object_retention_seconds=0, batch_size=10, max_batch_bytes=1024),
        activity_check=lambda: 'active_editor',
    )

    report = gc.collect(dry_run=False, now=NOW)

    assert report.deferred_reason == 'active_editor'
    assert store.object_path(persisted['artifact_hash']).is_file()
    assert db.get_object_record(persisted['artifact_hash'])['gc_state'] == 'active'
