from __future__ import annotations

import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_archive import ProjectExportMode, export_project_archive, import_project_archive
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root, require_project_root

REPORT_SOURCE = """import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import artifacts, assets

@app.cell
def _():
    count = artifacts.pull(name='count', data_type=int)
    return count

@app.cell
def _(count):
    artifacts.push(count * 2, name='doubled', data_type=int)
    assets.push(assets.Markdown(f'Count: {count}'), name='summary', title='Summary')
    assets.push(assets.DataFrame(pd.DataFrame({'value': [count, count * 2]})), name='table', title='Table', asset_type=assets.DataFrame)
    print('execution log excluded from snapshot')
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app
    run_notebook_app(app, __file__)
"""


def patch_graph(client: TestClient, operations: list[dict]) -> dict:
    version = client.get('/api/v1/graph').json()['meta']['graph_version']
    response = client.patch('/api/v1/graph', json={'graph_version': version, 'operations': operations})
    assert response.status_code == 200, response.text
    return response.json()


def run_notebook(client: TestClient, node_id: str, status: str = 'succeeded') -> str:
    response = client.post(f'/api/v1/nodes/{node_id}/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert response.status_code == 200, response.text
    run_id = response.json()['run_id']
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        snapshot = client.get('/api/v1/project/snapshot').json()
        run = next(entry for entry in snapshot['runs'] if entry['run_id'] == run_id)
        if run['status'] == status:
            return run_id
        time.sleep(0.05)
    raise AssertionError(f'Run did not reach {status}: {run}')


def database_rows(path: Path) -> dict[str, list[dict]]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]  # noqa: S608 - schema table names
            for table in tables
        }


@pytest.mark.parametrize('state', ['ready', 'stale', 'failed'])
def test_snapshot_round_trip_retains_exact_current_state_after_project_actions(tmp_path: Path, state: str) -> None:
    paths = init_project_root(tmp_path / 'project', project_id='snapshot-study')
    app = create_app(project_path=paths.root)
    with TestClient(app) as client:
        patch_graph(
            client,
            [
                {'type': 'add_pipeline_template', 'template_ref': 'builtin/example_movie_pipeline', 'x': 800, 'y': 400},
                {'type': 'add_constant_node', 'node_id': 'count', 'title': 'Count', 'data_type': 'int', 'value': 3},
                {'type': 'add_constant_node', 'node_id': 'undefined', 'title': 'Undefined', 'data_type': 'str'},
                {'type': 'add_constant_node', 'node_id': 'document', 'title': 'Document', 'data_type': 'file'},
                {'type': 'add_notebook_node', 'node_id': 'report', 'title': 'Report', 'source_text': REPORT_SOURCE},
                {
                    'type': 'add_notebook_node',
                    'node_id': 'deleted',
                    'title': 'Deleted',
                    'template_ref': 'builtin/value_input',
                },
            ],
        )
        assert (
            client.post(
                '/api/v1/constants/document/upload', content=b'Current document', headers={'X-Filename': 'document.txt'}
            ).status_code
            == 200
        )
        patch_graph(
            client,
            [
                {
                    'type': 'add_edge',
                    'source_node': 'count',
                    'source_port': 'value',
                    'target_node': 'report',
                    'target_port': 'count',
                }
            ],
        )
        old_run = run_notebook(client, 'report')
        deleted_run = run_notebook(client, 'deleted')
        assert client.post('/api/v1/checkpoints').status_code == 200
        assert client.post('/api/v1/constants/count/value', json={'value': 7}).status_code == 200
        current_run = run_notebook(client, 'report')
        patch_graph(client, [{'type': 'delete_node', 'node_id': 'deleted'}])
        assert (
            client.patch(
                '/api/v1/assets/report/summary/annotation',
                json={
                    'note': 'Keep this note',
                    'is_favorite': True,
                },
            ).status_code
            == 200
        )
        dashboard = client.post(
            '/api/v1/nodes/report/dashboards',
            json={
                'title': 'Saved report',
                'panels': [
                    {
                        'node_id': 'report',
                        'asset_name': 'table',
                        'visible': True,
                        'position': 0,
                        'modifier_overrides': {},
                    }
                ],
            },
        )
        assert dashboard.status_code == 200, dashboard.text
        if state == 'stale':
            assert client.post('/api/v1/constants/count/value', json={'value': 9}).status_code == 200
        elif state == 'failed':
            source = REPORT_SOURCE.replace(
                "    print('execution log excluded from snapshot')",
                "    raise RuntimeError('snapshot failure summary')",
            )
            paths.notebook_path('report').write_text(source, encoding='utf-8')
            app.state.container.project_service.reparse_notebook_by_path(paths.notebook_path('report'))
            current_run = run_notebook(client, 'report', 'failed')

        before_snapshot = client.get('/api/v1/project/snapshot').json()
        before_assets = client.get('/api/v1/nodes/report/assets').json()
        before_favorites = client.get('/api/v1/dashboards/@favorites').json()
        before_dashboard = client.get(f'/api/v1/dashboards/{dashboard.json()["dashboard_id"]}').json()
        before = database_rows(paths.state_db_path)
        assert before['node_tombstones']
        assert before['checkpoints']
        assert any(paths.execution_logs_dir.iterdir())
        # An unrelated file is never a part of saved managed state.
        (paths.metadata_dir / 'unrelated.txt').write_text('not part of the snapshot')
        (paths.uploads_dir / 'unused.txt').write_text('temporary upload')
        store = ObjectStore(paths)
        orphan = store.persist_value('unreferenced object', 'str')['artifact_hash']
        archive_path = tmp_path / 'snapshot.zip'
        export_project_archive(paths.root, archive_path, mode=ProjectExportMode.SNAPSHOT)
        # Export must leave source history and tombstone recovery intact.
        assert database_rows(paths.state_db_path) == before

    graph = before_snapshot['graph']
    live_ids = {node['id'] for node in graph['nodes']}
    live_incarnations = {node['incarnation_id'] for node in graph['nodes']}
    artifact_heads = [row for row in before['artifact_heads'] if row['node_id'] in live_ids]
    asset_heads = [row for row in before['asset_heads'] if row['node_id'] in live_ids]
    artifact_ids = {row['current_version_id'] for row in artifact_heads} - {None}
    asset_ids = {row['current_asset_version_id'] for row in asset_heads} - {None}
    artifact_versions = [row for row in before['artifact_versions'] if row['version_id'] in artifact_ids]
    asset_versions = [row for row in before['asset_versions'] if row['asset_version_id'] in asset_ids]
    asset_objects = [row for row in before['asset_version_objects'] if row['asset_version_id'] in asset_ids]
    hashes = {row['artifact_hash'] for row in artifact_versions + asset_objects}
    publication_ids = {row['publication_id'] for row in artifact_versions + asset_versions} - {None}
    assert orphan not in hashes
    assert len(before['artifact_versions']) > len(artifact_versions)
    assert len(before['asset_versions']) > len(asset_versions)

    expected_files = {
        'graph/meta.json',
        'graph/nodes.json',
        'graph/edges.json',
        'graph/layout.json',
        'metadata/project.json',
        'metadata/state.db',
        'pyproject.toml',
        'uv.lock',
        *(f'notebooks/{node["id"]}.py' for node in graph['nodes'] if node['kind'] == 'notebook'),
        *(f'dashboards/{node["id"]}.json' for node in graph['nodes'] if node['kind'] == 'dashboard'),
        *(f'objects/{digest[:2]}/{digest[2:]}' for digest in hashes),
    }
    extracted = tmp_path / 'extracted'
    with zipfile.ZipFile(archive_path) as archive:
        assert {info.filename for info in archive.infolist() if not info.is_dir()} == expected_files
        expected_directories = {'graph/', 'notebooks/', 'objects/', 'metadata/', 'checkpoints/', 'dashboards/'}
        expected_directories |= {f'objects/{digest[:2]}/' for digest in hashes}
        assert {info.filename for info in archive.infolist() if info.is_dir()} == expected_directories
        for member in expected_files - {'metadata/state.db'}:
            assert archive.read(member) == (paths.root / member).read_bytes()
        archive.extractall(extracted)
    require_project_root(extracted)
    after = database_rows(extracted / 'metadata/state.db')
    with sqlite3.connect(extracted / 'metadata/state.db') as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []

    expected = {
        'artifact_heads': artifact_heads,
        'asset_heads': asset_heads,
        'artifact_versions': artifact_versions,
        'asset_versions': asset_versions,
        'asset_version_objects': asset_objects,
        'node_incarnations': [row for row in before['node_incarnations'] if row['incarnation_id'] in live_incarnations],
        'asset_declarations': [row for row in before['asset_declarations'] if row['node_id'] in live_ids],
        'asset_annotations': [row for row in before['asset_annotations'] if row['node_id'] in live_ids],
        'notebook_execution_heads': [row for row in before['notebook_execution_heads'] if row['node_id'] in live_ids],
        'validation_issues': [row for row in before['validation_issues'] if row['node_id'] in live_ids],
        'persistent_notices': [
            row for row in before['persistent_notices'] if row['node_id'] is None or row['node_id'] in live_ids
        ],
        'publication_batches': [
            row for row in before['publication_batches'] if row['publication_id'] in publication_ids
        ],
        'run_inputs': [row for row in before['run_inputs'] if row['publication_id'] in publication_ids],
        'project_meta': [
            row
            for row in before['project_meta']
            if row['key']
            not in {
                'gc_last_report',
                'gc_last_error',
                'gc_last_completed_at',
                'gc_requested_at',
            }
        ],
    }
    latest_revisions = {
        row['node_id']: row for row in before['notebook_revisions'] if row['incarnation_id'] in live_incarnations
    }
    expected['notebook_revisions'] = list(latest_revisions.values())
    issue_ids = {row['issue_id'] for row in expected['validation_issues']}
    expected['validation_issue_dismissals'] = [
        row for row in before['validation_issue_dismissals'] if row['issue_id'] in issue_ids
    ]
    summaries = [
        row | {'stdout_path': None, 'stderr_path': None}
        for row in before['orchestrator_execution_meta']
        if row['node_id'] in live_ids
    ]
    expected['orchestrator_execution_meta'] = summaries
    run_ids = {row['run_id'] for row in summaries + expected['notebook_execution_heads']} - {None}
    expected['run_records'] = [row for row in before['run_records'] if row['run_id'] in run_ids]
    expected['objects'] = [
        row
        | {
            'gc_state': 'active',
            'gc_marked_at': None,
            'quarantined_at': None,
            'delete_after': None,
            'quarantine_path': None,
            'unreferenced_at': None,
        }
        for row in before['objects']
        if row['artifact_hash'] in hashes
    ]
    for table, rows in expected.items():
        assert sorted(after[table], key=repr) == sorted(rows, key=repr), table
    for table in after.keys() - expected.keys() - {'schema_migrations', 'sqlite_sequence'}:
        assert after[table] == [], table
    assert old_run not in {row['run_id'] for row in after['run_records']}
    assert deleted_run not in {row['run_id'] for row in after['run_records']}
    report_summary = next(row for row in after['orchestrator_execution_meta'] if row['node_id'] == 'report')
    assert report_summary['run_id'] == current_run
    if state == 'failed':
        assert report_summary['status'] == 'failed'
        assert 'snapshot failure summary' in report_summary['error']

    restored = tmp_path / 'restored'
    import_project_archive(archive_path, restored)
    require_project_root(restored)
    restored_store = ObjectStore(ProjectPaths(restored))
    doubled = next(row for row in artifact_versions if row['node_id'] == 'report' and row['artifact_name'] == 'doubled')
    assert restored_store.load_value(doubled['artifact_hash'], 'int') == 14
    with TestClient(create_app(project_path=restored)) as client:
        snapshot = client.get('/api/v1/project/snapshot').json()
        expected_graph = json.loads(json.dumps(graph))
        for node in expected_graph['nodes']:
            if node['execution_meta'] is not None:
                node['execution_meta']['stdout'] = None
                node['execution_meta']['stderr'] = None
        assert snapshot['graph'] == expected_graph
        assert snapshot['artifacts'] == before_snapshot['artifacts']
        assert client.get('/api/v1/nodes/report/assets').json() == before_assets
        assert client.get('/api/v1/dashboards/@favorites').json() == before_favorites
        assert client.get(f'/api/v1/dashboards/{dashboard.json()["dashboard_id"]}').json() == before_dashboard
        assert client.get('/api/v1/constants/count/value').json()['value'] == (9 if state == 'stale' else 7)
        assert client.get('/api/v1/artifacts/deleted/value').status_code == 404
        document = client.get('/api/v1/artifacts/document/value/download')
        assert document.status_code == 200
        assert document.content == b'Current document'
        # The restored project remains executable without historical objects.
        if state != 'failed':
            assert client.post('/api/v1/constants/count/value', json={'value': 11}).status_code == 200
            run_notebook(client, 'report')
            head = client.get('/api/v1/artifacts/report/doubled').json()
            assert restored_store.load_value(head['artifact_hash'], 'int') == 22


def test_snapshot_preserves_latest_parsed_contract_for_invalid_source(tmp_path: Path) -> None:
    paths = init_project_root(tmp_path / 'project')
    app = create_app(project_path=paths.root)
    with TestClient(app) as client:
        patch_graph(
            client,
            [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'broken',
                    'title': 'Broken',
                    'template_ref': 'builtin/value_input',
                }
            ],
        )
        paths.notebook_path('broken').write_text('def broken(:\n')
        app.state.container.project_service.reparse_notebook_by_path(paths.notebook_path('broken'))
        interface = client.get('/api/v1/project/snapshot').json()['graph']['nodes'][0]['interface']
        assert interface['issues'][0]['code'] == 'invalid_syntax'
        archive = tmp_path / 'snapshot.zip'
        export_project_archive(paths.root, archive, mode=ProjectExportMode.SNAPSHOT)
    restored = tmp_path / 'restored'
    import_project_archive(archive, restored)
    assert (restored / 'notebooks' / 'broken.py').read_text() == 'def broken(:\n'
    assert len(database_rows(restored / 'metadata/state.db')['notebook_revisions']) == 1
    with TestClient(create_app(project_path=restored)) as client:
        node = client.get('/api/v1/project/snapshot').json()['graph']['nodes'][0]
        assert node['interface'] | {'issues': []} == interface | {'issues': []}
        assert [issue | {'created_at': None} for issue in node['interface']['issues']] == [
            issue | {'created_at': None} for issue in interface['issues']
        ]
        assert client.get('/api/v1/artifacts/broken/value').json()['state'] == 'pending'


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'wrong_size', 'quarantined'])
def test_snapshot_checks_referenced_objects_and_recovers_quarantine(tmp_path: Path, damage: str) -> None:
    paths = init_project_root(tmp_path / 'project')
    with TestClient(create_app(project_path=paths.root)) as client:
        patch_graph(
            client,
            [{'type': 'add_constant_node', 'node_id': 'constant', 'title': 'Value', 'data_type': 'int', 'value': 42}],
        )
        digest = client.get('/api/v1/artifacts/constant/value').json()['artifact_hash']
        relative = Path(digest[:2]) / digest[2:]
        blob = paths.object_store_dir / relative
        if damage == 'missing':
            blob.unlink()
        elif damage == 'corrupt':
            blob.chmod(0o644)
            blob.write_bytes(b'43')  # Same byte count, incorrect checksum.
        elif damage == 'wrong_size':
            with sqlite3.connect(paths.state_db_path) as connection:
                connection.execute('UPDATE objects SET size_bytes = size_bytes + 1 WHERE artifact_hash = ?', (digest,))
        else:
            quarantine = paths.object_quarantine_dir / relative
            quarantine.parent.mkdir(parents=True)
            blob.rename(quarantine)
            with sqlite3.connect(paths.state_db_path) as connection:
                connection.execute(
                    "UPDATE objects SET gc_state = 'quarantined', quarantine_path = ? WHERE artifact_hash = ?",
                    (str(quarantine), digest),
                )
        archive = tmp_path / 'snapshot.zip'
        if damage == 'quarantined':
            export_project_archive(paths.root, archive, mode=ProjectExportMode.SNAPSHOT)
            restored = tmp_path / 'restored'
            import_project_archive(archive, restored)
            assert ObjectStore(ProjectPaths(restored)).load_value(digest, 'int') == 42
            assert quarantine.exists()
        else:
            with pytest.raises(ProjectValidationError, match=r'Snapshot object is (missing|corrupt)'):
                export_project_archive(paths.root, archive, mode=ProjectExportMode.SNAPSHOT)
            assert not archive.exists()
