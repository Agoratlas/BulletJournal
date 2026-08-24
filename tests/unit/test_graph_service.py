from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode
from bulletjournal.domain.errors import GraphValidationError, TombstoneExpiredError
from bulletjournal.domain.hashing import combine_hashes
from bulletjournal.domain.models import CheckpointRecord
from bulletjournal.services.checkpoint_service import CheckpointService
from bulletjournal.services.dashboard_service import DashboardService
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.notebook_service import NotebookService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def publish(self, *args, **kwargs) -> None:
        _ = (args, kwargs)


def test_delete_restore_conflict_restart_and_redo_preserve_incarnation(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)
    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_area_node', 'node_id': 'same', 'title': 'Original'}],
        request_id='create-original',
    )
    original_incarnation = created['graph']['nodes'][0]['incarnation_id']
    deleted = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [{'type': 'delete_node', 'node_id': 'same'}],
        request_id='delete-original',
    )
    mutation = deleted['tombstone_mutations'][0]
    assert mutation['incarnation_id'] == original_incarnation
    assert (
        graph_service.apply_operations(
            int(created['graph']['meta']['graph_version']),
            [{'type': 'delete_node', 'node_id': 'same'}],
            request_id='delete-original',
        )['tombstone_mutations']
        == deleted['tombstone_mutations']
    )

    replacement = graph_service.apply_operations(
        int(deleted['graph']['meta']['graph_version']),
        [{'type': 'add_area_node', 'node_id': 'same', 'title': 'Replacement'}],
    )
    assert replacement['graph']['nodes'][0]['incarnation_id'] != original_incarnation
    with pytest.raises(GraphValidationError, match='already exists'):
        graph_service.restore_tombstone(mutation['tombstone_id'])

    renamed = graph_service.apply_operations(
        int(replacement['graph']['meta']['graph_version']),
        [{'type': 'rename_node', 'node_id': 'same', 'new_node_id': 'replacement', 'title': 'Replacement'}],
    )
    project_service.watcher.stop()
    reopened = ProjectService(_FakeEventService(), TemplateService())
    reopened.open_project(project_root)
    reopened_graph_service = GraphService(reopened)
    restored = reopened_graph_service.restore_tombstone(mutation['tombstone_id'], request_id='restore-original')
    restored_node = next(node for node in restored['graph']['nodes'] if node['id'] == 'same')
    assert restored_node['incarnation_id'] == original_incarnation

    redone = reopened_graph_service.redo_tombstone(
        mutation['tombstone_id'], original_incarnation, request_id='redo-original'
    )
    assert all(node['incarnation_id'] != original_incarnation for node in redone['graph']['nodes'])
    assert renamed['graph']['nodes'][0]['incarnation_id'] != original_incarnation


def test_constant_delete_restores_exact_ready_version_and_expiry_is_controlled(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)
    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_constant_node', 'node_id': 'value', 'title': 'Value', 'data_type': 'int', 'value': 42}],
    )
    before = project_service.require_project().state_db.get_artifact_head('value', 'value')
    assert before is not None and before['state'] == 'ready'
    deleted = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']), [{'type': 'delete_node', 'node_id': 'value'}]
    )
    mutation = deleted['tombstone_mutations'][0]
    assert project_service.require_project().state_db.get_artifact_head('value', 'value') is None
    graph_service.restore_tombstone(mutation['tombstone_id'])
    after = project_service.require_project().state_db.get_artifact_head('value', 'value')
    assert after is not None
    assert after['current_version_id'] == before['current_version_id']
    assert after['artifact_hash'] == before['artifact_hash']

    project_service.require_project().state_db.set_project_meta('gc_tombstone_retention_seconds', '0')
    graph = project_service.graph()
    expired = graph_service.apply_operations(
        int(graph.meta['graph_version']), [{'type': 'delete_node', 'node_id': 'value'}]
    )['tombstone_mutations'][0]
    with pytest.raises(TombstoneExpiredError):
        graph_service.restore_tombstone(expired['tombstone_id'])


def test_constant_value_can_be_loaded_for_copying(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)
    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_constant_node', 'node_id': 'value', 'data_type': 'dict', 'value': {'count': 42}}],
    )

    from bulletjournal.services.artifact_service import ArtifactService

    assert ArtifactService(project_service).get_constant_value('value') == {'count': 42}


def _attach_checkpoint_service(project_service: ProjectService) -> CheckpointService:
    checkpoint_service = CheckpointService(project_service)
    project_service.checkpoint_service = checkpoint_service
    return checkpoint_service


def _consumer_source() -> str:
    return (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    sample_count = artifacts.pull(name='sample_count', data_type=int)
    return sample_count

@app.cell
def _(sample_count):
    artifacts.push(sample_count * 2, name='doubled', data_type=int)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )


def _asset_source() -> str:
    return (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='notes', title='Notes')
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )


def _consumer_with_asset_source() -> str:
    return (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts, assets

@app.cell
def _():
    source_value = artifacts.pull(name='source_value', data_type=int)
    return source_value

@app.cell
def _(source_value):
    artifacts.push(source_value * 2, name='doubled', data_type=int)
    assets.push(assets.Markdown(str(source_value)), name='notes', title='Notes')
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )


def test_notebook_tombstone_restores_dashboard_panel_and_exact_ready_asset_version(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_notebook_node', 'node_id': 'report', 'title': 'Report', 'source_text': _asset_source()}],
    )
    interface = project_service.latest_interface('report')
    assert interface is not None
    source_hash = interface['source_hash']
    expected_data_hash = combine_hashes([source_hash, 'report/notes'])
    expected_code_hash = combine_hashes([source_hash, 'report/notes'])
    db = project_service.require_project().state_db
    asset_version_id = db.create_asset_version(
        node_id='report',
        asset_name='notes',
        asset_type='markdown',
        interactive=False,
        source_hash=source_hash,
        upstream_code_hash=expected_code_hash,
        upstream_data_hash=expected_data_hash,
        run_id='run-report',
        lineage_mode=LineageMode.MANAGED,
        definition={'asset_type': 'markdown', 'markdown_text': 'hello'},
        modifier_schema=[{'name': 'theme', 'type': 'string'}],
        default_modifiers={'theme': 'light'},
        override_schema_hash='schema-notes',
        warnings=[],
        objects=[],
    )
    panel = {
        'panel_id': 'report/notes',
        'node_id': 'report',
        'asset_name': 'notes',
        'visible': False,
        'position': 0,
        'modifier_overrides': {'theme': 'dark'},
        'override_schema_hash': 'schema-notes',
    }
    dashboard = project_service.dashboard_service.create_dashboard(
        dashboard_id='report_dashboard',
        title='Report Dashboard',
        sources=[{'node_id': 'report'}],
        panels=[panel],
    )

    deleted = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'delete_node', 'node_id': 'report'}],
    )
    assert project_service.dashboard_service.get_dashboard('report_dashboard')['sources'] == []

    graph_service.restore_tombstone(deleted['tombstone_mutations'][0]['tombstone_id'])

    restored_dashboard = project_service.dashboard_service.get_dashboard('report_dashboard')
    assert restored_dashboard['sources'] == dashboard['sources']
    assert restored_dashboard['panels'] == dashboard['panels']
    restored_asset = db.get_asset_head('report', 'notes')
    assert restored_asset is not None
    assert restored_asset['current_asset_version_id'] == asset_version_id
    assert restored_asset['state'] == ArtifactState.READY.value


def test_notebook_tombstone_keeps_outputs_stale_when_restored_input_is_stale(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {'type': 'add_constant_node', 'node_id': 'source', 'title': 'Source', 'data_type': 'int', 'value': 21},
            {
                'type': 'add_notebook_node',
                'node_id': 'consumer',
                'title': 'Consumer',
                'source_text': _consumer_with_asset_source(),
            },
        ],
    )
    connected = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'add_edge',
                'source_node': 'source',
                'source_port': 'value',
                'target_node': 'consumer',
                'target_port': 'source_value',
            }
        ],
    )
    db = project_service.require_project().state_db
    source_head = db.get_artifact_head('source', 'value')
    interface = project_service.latest_interface('consumer')
    assert source_head is not None and interface is not None
    source_hash = interface['source_hash']
    input_hash = source_head['artifact_hash']
    input_code_hash = source_head['upstream_code_hash']
    db.upsert_artifact_object(
        'consumer-output-hash',
        'json',
        'int',
        2,
        None,
        None,
        {'kind': 'simple', 'repr': '42', 'truncated': False},
    )
    artifact_version_id = db.create_artifact_version(
        node_id='consumer',
        artifact_name='doubled',
        role=ArtifactRole.OUTPUT,
        artifact_hash='consumer-output-hash',
        source_hash=source_hash,
        upstream_code_hash=combine_hashes([source_hash, 'consumer/doubled', input_code_hash]),
        upstream_data_hash=combine_hashes([source_hash, 'consumer/doubled', input_hash]),
        run_id='run-consumer',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    asset_version_id = db.create_asset_version(
        node_id='consumer',
        asset_name='notes',
        asset_type='markdown',
        interactive=False,
        source_hash=source_hash,
        upstream_code_hash=combine_hashes([source_hash, 'consumer/notes', input_code_hash]),
        upstream_data_hash=combine_hashes([source_hash, 'consumer/notes', input_hash]),
        run_id='run-consumer',
        lineage_mode=LineageMode.MANAGED,
        definition={'asset_type': 'markdown', 'markdown_text': '21'},
        modifier_schema=[],
        default_modifiers={},
        override_schema_hash='schema-notes',
        warnings=[],
        objects=[],
    )
    deleted = graph_service.apply_operations(
        int(connected['graph']['meta']['graph_version']),
        [{'type': 'delete_node', 'node_id': 'consumer'}],
    )
    db.set_artifact_head_state('source', 'value', ArtifactState.STALE)

    restored = graph_service.restore_tombstone(deleted['tombstone_mutations'][0]['tombstone_id'])

    assert any(
        edge['source_node'] == 'source'
        and edge['source_port'] == 'value'
        and edge['target_node'] == 'consumer'
        and edge['target_port'] == 'source_value'
        for edge in restored['graph']['edges']
    )
    restored_artifact = db.get_artifact_head('consumer', 'doubled')
    restored_asset = db.get_asset_head('consumer', 'notes')
    assert restored_artifact is not None
    assert restored_artifact['current_version_id'] == artifact_version_id
    assert restored_artifact['state'] == ArtifactState.STALE.value
    assert restored_asset is not None
    assert restored_asset['current_asset_version_id'] == asset_version_id
    assert restored_asset['state'] == ArtifactState.STALE.value


def test_graph_service_restores_deleted_notebook_and_edges_in_same_request(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'source',
                'title': 'Source',
                'template_ref': 'builtin/value_input',
            },
            {
                'type': 'add_notebook_node',
                'node_id': 'consumer',
                'title': 'Consumer',
                'source_text': _consumer_source(),
                'x': 480,
                'y': 80,
            },
        ],
    )

    connected = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'add_edge',
                'source_node': 'source',
                'source_port': 'value',
                'target_node': 'consumer',
                'target_port': 'sample_count',
            }
        ],
    )

    deleted = graph_service.apply_operations(
        int(connected['graph']['meta']['graph_version']),
        [
            {
                'type': 'delete_node',
                'node_id': 'consumer',
            }
        ],
    )

    restored = graph_service.apply_operations(
        int(deleted['graph']['meta']['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'consumer',
                'title': 'Consumer',
                'source_text': _consumer_source(),
                'x': 480,
                'y': 80,
            },
            {
                'type': 'add_edge',
                'source_node': 'source',
                'source_port': 'value',
                'target_node': 'consumer',
                'target_port': 'sample_count',
            },
        ],
    )

    assert any(
        edge['source_node'] == 'source'
        and edge['source_port'] == 'value'
        and edge['target_node'] == 'consumer'
        and edge['target_port'] == 'sample_count'
        for edge in restored['graph']['edges']
    )
    snapshot = project_service.snapshot()
    consumer = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'consumer')
    assert consumer['interface'] is not None
    assert [port['name'] for port in consumer['interface']['inputs']] == ['sample_count']


def test_graph_service_renames_notebook_app_title_with_new_node_id(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
                'template_ref': 'builtin/test_starter_notebook',
            }
        ],
    )

    renamed = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'rename_node',
                'node_id': 'sample_node',
                'new_node_id': 'renamed_node',
                'title': 'Sample Node',
            }
        ],
    )

    source = (project_root / 'notebooks' / 'renamed_node.py').read_text(encoding='utf-8')
    assert "app_title='renamed_node'" in source
    assert not (project_root / 'notebooks' / 'sample_node.py').exists()
    assert any(node['id'] == 'renamed_node' for node in renamed['graph']['nodes'])


def test_graph_service_deletes_marimo_session_cache_for_deleted_notebook(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    session_cache = project_root / 'notebooks' / '__marimo__' / 'session' / 'sample_node.py.json'
    session_cache.parent.mkdir(parents=True, exist_ok=True)
    session_cache.write_text('{"cells": []}', encoding='utf-8')

    graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'delete_node',
                'node_id': 'sample_node',
            }
        ],
    )

    assert not session_cache.exists()


def test_graph_service_deletes_marimo_session_cache_for_renamed_notebook(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    marimo_session_dir = project_root / 'notebooks' / '__marimo__' / 'session'
    marimo_session_dir.mkdir(parents=True, exist_ok=True)
    old_session_cache = marimo_session_dir / 'sample_node.py.json'
    new_session_cache = marimo_session_dir / 'renamed_node.py.json'
    old_session_cache.write_text('{"cells": ["old"]}', encoding='utf-8')
    new_session_cache.write_text('{"cells": ["stale"]}', encoding='utf-8')

    graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'rename_node',
                'node_id': 'sample_node',
                'new_node_id': 'renamed_node',
                'title': 'Sample Node',
            }
        ],
    )

    assert not old_session_cache.exists()
    assert not new_session_cache.exists()


def test_snapshot_treats_empty_notebook_as_custom(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'custom_notebook',
                'title': 'Custom Notebook',
                'template_ref': 'builtin/empty_notebook',
            }
        ],
    )

    snapshot = project_service.snapshot()
    notebook = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'custom_notebook')

    assert notebook['template'] is None
    assert notebook['template_status'] is None


def test_snapshot_keeps_new_template_notebook_as_template_until_edited(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
                'template_ref': 'builtin/test_starter_notebook',
            }
        ],
    )

    snapshot = project_service.snapshot()
    notebook = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'sample_node')
    assert notebook['template']['ref'] == 'builtin/test_starter_notebook'
    assert notebook['template_status'] == 'template'

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(source.replace('Sample output frame', 'Modified output frame'), encoding='utf-8')
    NotebookService(project_service).reparse_notebook('sample_node')

    updated_snapshot = project_service.snapshot()
    updated_notebook = next(node for node in updated_snapshot['graph']['nodes'] if node['id'] == 'sample_node')
    assert updated_notebook['template_status'] == 'modified'


def test_snapshot_uses_cached_template_app_definition(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)
    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
                'template_ref': 'builtin/test_starter_notebook',
            }
        ],
    )

    def fail_parse(*args, **kwargs):
        raise AssertionError('Snapshot must reuse the parsed template app definition.')

    monkeypatch.setattr('bulletjournal.templates.notebook_source.ast.parse', fail_parse)

    snapshot = project_service.snapshot()

    notebook = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'sample_node')
    assert notebook['template_status'] == 'template'


def test_default_new_notebook_includes_assets_example(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    source = notebook_path.read_text(encoding='utf-8')

    assert 'from bulletjournal.runtime import artifacts, assets' in source
    assert "description='How many sample rows to generate'" in source
    assert 'assets.DataFrame(frame)' in source
    assert "name='sample_table'" in source


def test_graph_service_blocks_notebook_id_change_while_editor_is_open(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda node_id: object() if node_id == 'sample_node' else None),
        orchestrator_state=lambda: {},
    )

    with pytest.raises(GraphValidationError, match='editor is open'):
        graph_service.apply_operations(
            int(created['graph']['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Renamed sample',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['graph']['nodes'])


def test_graph_service_blocks_notebook_id_change_while_queued(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda _node_id: None),
        orchestrator_state=lambda: {'sample_node': {'status': 'queued'}},
    )

    with pytest.raises(GraphValidationError, match='queued for execution'):
        graph_service.apply_operations(
            int(created['graph']['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Sample Node',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['graph']['nodes'])


def test_graph_service_blocks_notebook_id_change_while_running(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda _node_id: None),
        orchestrator_state=lambda: {'sample_node': {'status': 'running'}},
    )

    with pytest.raises(GraphValidationError, match='while it is running'):
        graph_service.apply_operations(
            int(created['graph']['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Sample Node',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['graph']['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['graph']['nodes'])


def test_graph_service_materializes_dashboard_nodes_from_pipeline_templates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    template_source = SimpleNamespace(
        source_text=(
            """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='notes', title='Notes')
    return
""".strip()
            + '\n'
        ),
        source_hash='hash',
    )
    project_service.template_service.resolve_pipeline_template = lambda ref, allow_inactive=False: SimpleNamespace(
        definition={
            'nodes': [
                {'id': 'analysis', 'title': 'Analysis', 'kind': 'notebook', 'template_ref': 'builtin/example'},
                {
                    'id': 'dashboard_view',
                    'title': 'Dashboard View',
                    'kind': 'dashboard',
                    'dashboard': {
                        'sources': [{'node_id': 'analysis'}],
                        'panels': [
                            {
                                'panel_id': 'analysis/notes',
                                'node_id': 'analysis',
                                'asset_name': 'notes',
                                'visible': True,
                                'position': 0,
                                'modifier_overrides': {},
                            }
                        ],
                    },
                },
            ],
            'edges': [],
            'layout': [
                {'node_id': 'analysis', 'x': 80, 'y': 220, 'w': 320, 'h': 220},
                {'node_id': 'dashboard_view', 'x': 80, 'y': 0, 'w': 360, 'h': 220},
            ],
        }
    )
    project_service.template_service.resolve_template_source = lambda ref, allow_inactive=False: template_source
    project_service.template_service.template_ref = lambda ref: SimpleNamespace(
        kind='notebook',
        provider='builtin',
        name='example',
        ref=ref,
        origin_revision=None,
        to_dict=lambda: {
            'kind': 'notebook',
            'provider': 'builtin',
            'name': 'example',
            'ref': ref,
            'origin_revision': None,
        },
    )
    project_service.template_service.pipeline_node_interfaces = lambda definition: {
        'analysis': {'inputs': [], 'outputs': []},
        'dashboard_view': {'inputs': [], 'outputs': []},
    }
    project_service.template_service.list_templates = lambda: []

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_pipeline_template',
                'template_ref': 'builtin/pipeline_with_dashboard',
                'x': 80,
                'y': 80,
                'node_id_suffix': 'copy',
            }
        ],
    )

    assert any(
        node['id'] == 'dashboard_view_copy' and node['kind'] == 'dashboard' for node in created['graph']['nodes']
    )
    assert any(node['id'] == 'analysis_copy' and node['title'] == 'Analysis' for node in created['graph']['nodes'])
    assert any(
        node['id'] == 'dashboard_view_copy' and node['title'] == 'Dashboard View' for node in created['graph']['nodes']
    )
    dashboard_payload = project_service.dashboard_service.get_dashboard('dashboard_view_copy')
    assert dashboard_payload['sources'] == [{'node_id': 'analysis_copy'}]
    assert dashboard_payload['panels'][0]['node_id'] == 'analysis_copy'
    assert dashboard_payload['panels'][0]['panel_id'] == 'analysis_copy/notes'


def test_pipeline_dashboard_materialization_preserves_template_panels_after_reparse(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    def notebook_template(asset_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            source_text=(
                f"""
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='{asset_name}', title='{asset_name}')
    return
""".strip()
                + '\n'
            ),
            source_hash=f'{asset_name}-hash',
        )

    template_sources = {
        'builtin/alpha': notebook_template('alpha_asset'),
        'builtin/beta': notebook_template('beta_asset'),
    }
    project_service.template_service.resolve_pipeline_template = lambda ref, allow_inactive=False: SimpleNamespace(
        definition={
            'nodes': [
                {'id': 'alpha', 'title': 'Alpha', 'kind': 'notebook', 'template_ref': 'builtin/alpha'},
                {'id': 'beta', 'title': 'Beta', 'kind': 'notebook', 'template_ref': 'builtin/beta'},
                {
                    'id': 'dashboard_view',
                    'title': 'Dashboard View',
                    'kind': 'dashboard',
                    'dashboard': {
                        'sources': [{'node_id': 'alpha'}, {'node_id': 'beta'}],
                        'panels': [
                            {
                                'node_id': 'beta',
                                'asset_name': 'beta_asset',
                                'visible': False,
                                'position': 0,
                            },
                            {
                                'node_id': 'alpha',
                                'asset_name': 'alpha_asset',
                                'visible': True,
                                'position': 1,
                            },
                        ],
                    },
                },
            ],
            'edges': [],
            'layout': [
                {'node_id': 'alpha', 'x': 80, 'y': 220, 'w': 320, 'h': 220},
                {'node_id': 'beta', 'x': 440, 'y': 220, 'w': 320, 'h': 220},
                {'node_id': 'dashboard_view', 'x': 80, 'y': 0, 'w': 360, 'h': 220},
            ],
        }
    )
    project_service.template_service.resolve_template_source = lambda ref, allow_inactive=False: template_sources[ref]
    project_service.template_service.template_ref = lambda ref: SimpleNamespace(
        kind='notebook',
        provider='builtin',
        name=ref.rsplit('/', 1)[-1],
        ref=ref,
        origin_revision=None,
        to_dict=lambda: {
            'kind': 'notebook',
            'provider': 'builtin',
            'name': ref.rsplit('/', 1)[-1],
            'ref': ref,
            'origin_revision': None,
        },
    )
    project_service.template_service.pipeline_node_interfaces = lambda definition: {
        'alpha': {'inputs': [], 'outputs': []},
        'beta': {'inputs': [], 'outputs': []},
        'dashboard_view': {'inputs': [], 'outputs': []},
    }
    project_service.template_service.list_templates = lambda: []

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_pipeline_template',
                'template_ref': 'builtin/pipeline_with_dashboard',
                'x': 80,
                'y': 80,
            }
        ],
    )

    dashboard = project_service.dashboard_service.get_dashboard('dashboard_view')
    assert dashboard['version'] == 1
    assert [(panel['panel_id'], panel['visible']) for panel in dashboard['panels']] == [
        ('beta/beta_asset', False),
        ('alpha/alpha_asset', True),
    ]


def test_pipeline_dashboard_materialization_rejects_incomplete_asset_set(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    project_service.dashboard_service = DashboardService(project_service)
    graph_service = GraphService(project_service)

    template_source = SimpleNamespace(
        source_text=(
            """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='included', title='Included')
    assets.push(assets.Markdown('hello'), name='missing', title='Missing')
    return
""".strip()
            + '\n'
        ),
        source_hash='hash',
    )
    project_service.template_service.resolve_pipeline_template = lambda ref, allow_inactive=False: SimpleNamespace(
        definition={
            'nodes': [
                {'id': 'analysis', 'title': 'Analysis', 'kind': 'notebook', 'template_ref': 'builtin/example'},
                {
                    'id': 'dashboard_view',
                    'title': 'Dashboard View',
                    'kind': 'dashboard',
                    'dashboard': {
                        'sources': [{'node_id': 'analysis'}],
                        'panels': [{'node_id': 'analysis', 'asset_name': 'included', 'position': 0}],
                    },
                },
            ],
            'edges': [],
            'layout': [
                {'node_id': 'analysis', 'x': 80, 'y': 220, 'w': 320, 'h': 220},
                {'node_id': 'dashboard_view', 'x': 80, 'y': 0, 'w': 360, 'h': 220},
            ],
        }
    )
    project_service.template_service.resolve_template_source = lambda ref, allow_inactive=False: template_source
    project_service.template_service.template_ref = lambda ref: SimpleNamespace(
        kind='notebook',
        provider='builtin',
        name='example',
        ref=ref,
        origin_revision=None,
        to_dict=lambda: {
            'kind': 'notebook',
            'provider': 'builtin',
            'name': 'example',
            'ref': ref,
            'origin_revision': None,
        },
    )
    project_service.template_service.pipeline_node_interfaces = lambda definition: {
        'analysis': {'inputs': [], 'outputs': []},
        'dashboard_view': {'inputs': [], 'outputs': []},
    }
    project_service.template_service.list_templates = lambda: []

    with pytest.raises(GraphValidationError, match='asset set does not match its sources'):
        graph_service.apply_operations(
            int(project_service.graph().meta['graph_version']),
            [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/pipeline_with_dashboard',
                    'x': 80,
                    'y': 80,
                }
            ],
        )


def test_graph_changes_create_automatic_checkpoint_when_last_is_older_than_ten_minutes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    checkpoint_service = _attach_checkpoint_service(project_service)
    graph_service = GraphService(project_service)

    old_created_at = (
        (datetime.now(tz=UTC) - timedelta(minutes=11)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    )
    monkeypatch.setattr(
        project_service.require_project().state_db,
        'list_checkpoints',
        lambda: [
            CheckpointRecord(
                checkpoint_id='existing', created_at=old_created_at, graph_version=1, path='x', restored_at=None
            )
        ],
    )

    calls: list[str] = []

    def record_create_checkpoint() -> dict[str, object]:
        calls.append('created')
        return {'checkpoint_id': 'auto', 'path': 'x', 'graph_version': 1}

    monkeypatch.setattr(checkpoint_service, 'create_checkpoint', record_create_checkpoint)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    assert calls == ['created']


def test_graph_changes_skip_automatic_checkpoint_when_recent_checkpoint_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    checkpoint_service = _attach_checkpoint_service(project_service)
    graph_service = GraphService(project_service)

    recent_created_at = (
        (datetime.now(tz=UTC) - timedelta(minutes=9)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    )
    monkeypatch.setattr(
        project_service.require_project().state_db,
        'list_checkpoints',
        lambda: [
            CheckpointRecord(
                checkpoint_id='recent', created_at=recent_created_at, graph_version=1, path='x', restored_at=None
            )
        ],
    )

    calls: list[str] = []

    def record_create_checkpoint() -> dict[str, object]:
        calls.append('created')
        return {'checkpoint_id': 'auto', 'path': 'x', 'graph_version': 1}

    monkeypatch.setattr(checkpoint_service, 'create_checkpoint', record_create_checkpoint)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    assert calls == []


def test_notebook_source_changes_create_automatic_checkpoint_when_due(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    checkpoint_service = _attach_checkpoint_service(project_service)
    graph_service = GraphService(project_service)

    graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
                'template_ref': 'builtin/test_starter_notebook',
            }
        ],
    )

    old_created_at = (
        (datetime.now(tz=UTC) - timedelta(minutes=11)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    )
    monkeypatch.setattr(
        project_service.require_project().state_db,
        'list_checkpoints',
        lambda: [
            CheckpointRecord(
                checkpoint_id='existing', created_at=old_created_at, graph_version=1, path='x', restored_at=None
            )
        ],
    )

    calls: list[str] = []

    def record_create_checkpoint() -> dict[str, object]:
        calls.append('created')
        return {'checkpoint_id': 'auto', 'path': 'x', 'graph_version': 1}

    monkeypatch.setattr(checkpoint_service, 'create_checkpoint', record_create_checkpoint)

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(source.replace('Sample output frame', 'Modified output frame'), encoding='utf-8')

    NotebookService(project_service).reparse_notebook('sample_node')

    assert calls == ['created']
