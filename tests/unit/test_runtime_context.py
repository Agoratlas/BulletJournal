import time
from pathlib import Path

import pandas as pd
import pytest

import bulletjournal.runtime.assets as runtime_assets
import bulletjournal.runtime.context as runtime_context
from bulletjournal.assets.types.scatter_plot import MAX_SCATTER_PLOT_POINTS
from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode
from bulletjournal.domain.hashing import combine_hashes, hash_json
from bulletjournal.domain.models import AssetDeclaration, Port
from bulletjournal.runtime.context import (
    _RUNTIME_CONTEXT,
    Binding,
    RuntimeContext,
    _format_publication_supersession,
    current_runtime_context,
)
from bulletjournal.storage.project_fs import init_project_root


@pytest.fixture(autouse=True)
def skip_interactive_stabilization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_context, 'EDIT_STABILIZATION_SECONDS', 0)


def test_runtime_context_interactive_stabilization_waits_for_stable_notebook(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'consumer.py'
    notebook_path.write_text('print("stable")\n', encoding='utf-8')
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-stabilization',
        source_hash='stale-source-hash',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={},
        outputs={},
    )
    monkeypatch.setattr(runtime_context, 'EDIT_STABILIZATION_SECONDS', 0.2)

    started_at = time.monotonic()
    context._stabilize_if_interactive()

    assert time.monotonic() - started_at >= 0.2
    assert context.source_hash != 'stale-source-hash'


def test_runtime_context_uses_defaults_without_recording_stale_warning(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-default',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'sample_count': Binding(
                source_node='',
                source_artifact='',
                data_type='int',
                default=10,
                has_default=True,
            )
        },
        outputs={},
    )

    metadata = context.resolve_pull('sample_count')

    assert metadata['value'] == 10
    assert metadata['artifact_hash'] == hash_json(10)
    assert metadata['upstream_code_hash'] == 'default'
    assert metadata['state'] == ArtifactState.READY.value
    assert metadata['warnings'] == []


def test_publication_supersession_message_includes_input_versions_and_dates() -> None:
    message = _format_publication_supersession(
        {
            'expected_source_hash': 'source-hash',
            'actual_source_hash': 'source-hash',
            'expected_generation': 3,
            'actual_generation': 3,
            'actual_incarnation_status': 'live',
            'inputs': [
                {
                    'artifact': 'producer/value',
                    'expected_version_id': 12,
                    'expected_hash': 'old-hash',
                    'expected_state': ArtifactState.READY.value,
                    'loaded_at': '2026-08-11T10:00:00Z',
                    'actual_version_id': 13,
                    'actual_hash': 'new-hash',
                    'actual_state': ArtifactState.READY.value,
                    'actual_created_at': '2026-08-11T10:01:00Z',
                }
            ],
        }
    )

    assert 'producer/value' in message
    assert 'expected version 12 (ready) loaded at 2026-08-11T10:00:00Z' in message
    assert 'actual version 13 (ready) created at 2026-08-11T10:01:00Z' in message


def test_runtime_context_validates_pull_contract_for_default_backed_input(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-default-contract',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'sample_count': Binding(
                source_node='',
                source_artifact='',
                data_type='int',
                default=None,
                has_default=True,
            )
        },
        outputs={},
    )

    context.validate_pull_contract(name='sample_count', data_type='int')


def test_runtime_context_resolves_optional_missing_file_input(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-default-file',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'dataset': Binding(
                source_node='',
                source_artifact='',
                data_type='file',
                default=None,
                has_default=True,
            )
        },
        outputs={},
    )

    metadata = context.resolve_pull_file('dataset')

    assert metadata['path'] is None
    assert metadata['artifact_hash'] == hash_json(None)
    assert metadata['upstream_code_hash'] == 'default'
    assert metadata['state'] == ArtifactState.READY.value
    assert metadata['warnings'] == []


def test_runtime_context_resolves_file_input_with_declared_extension(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-file-extension',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'dataset': Binding(
                source_node='producer',
                source_artifact='report',
                data_type='file',
            )
        },
        outputs={},
    )

    uploaded_file = tmp_path / 'report-upload'
    uploaded_file.write_text('alpha,beta\n1,2\n', encoding='utf-8')
    persisted = context.object_store.persist_file(uploaded_file, extension='.csv')
    context.db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='report',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code',
        upstream_data_hash='producer-data',
        run_id='upstream-run',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    metadata = context.resolve_pull_file('dataset')

    resolved_path = Path(metadata['path'])
    assert resolved_path.parent == context.paths.pulled_files_dir
    assert resolved_path.suffix == '.csv'
    assert resolved_path.read_text(encoding='utf-8') == 'alpha,beta\n1,2\n'


def test_runtime_context_missing_binding_error_includes_guidance(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-missing-binding',
        source_hash='source-hash',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'incoming': Binding(
                source_node='',
                source_artifact='',
                data_type='int',
            )
        },
        outputs={},
    )

    with pytest.raises(FileNotFoundError, match='Please ensure you have connected an input or set a default value'):
        context.resolve_pull('incoming')


def test_runtime_context_interactive_refresh_surfaces_parse_error_for_pull(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'consumer.py'
    notebook_path.write_text(
        """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    broken =
""",
        encoding='utf-8',
    )
    graph_dir = project_root / 'graph'
    (graph_dir / 'meta.json').write_text(
        '{\n  "schema_version": 1,\n  "project_id": "project",\n  "graph_version": 2,\n  "updated_at": "2026-03-26T00:00:00Z"\n}\n',
        encoding='utf-8',
    )
    (graph_dir / 'nodes.json').write_text(
        '[\n  {"id": "consumer", "kind": "notebook", "title": "Consumer", "path": "notebooks/consumer.py", "template": null, "ui": {}}\n]\n',
        encoding='utf-8',
    )
    (graph_dir / 'edges.json').write_text('[]\n', encoding='utf-8')
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-parse-error',
        source_hash='old-source-hash',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={},
        outputs={},
    )

    with pytest.raises(KeyError, match='could not be reparsed') as exc_info:
        context.resolve_pull('incoming')

    assert 'Syntax error on line 10, column 13: invalid syntax.' in str(exc_info.value)
    assert 'Offending code: `broken =`.' in str(exc_info.value)


def test_runtime_context_interactive_refresh_surfaces_parse_error_for_asset_push(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'consumer.py'
    notebook_path.write_text(
        """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    broken =
""",
        encoding='utf-8',
    )
    graph_dir = project_root / 'graph'
    (graph_dir / 'meta.json').write_text(
        '{\n  "schema_version": 1,\n  "project_id": "project",\n  "graph_version": 2,\n  "updated_at": "2026-03-26T00:00:00Z"\n}\n',
        encoding='utf-8',
    )
    (graph_dir / 'nodes.json').write_text(
        '[\n  {"id": "consumer", "kind": "notebook", "title": "Consumer", "path": "notebooks/consumer.py", "template": null, "ui": {}}\n]\n',
        encoding='utf-8',
    )
    (graph_dir / 'edges.json').write_text('[]\n', encoding='utf-8')
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-asset-error',
        source_hash='old-source-hash',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={},
        outputs={},
        asset_declarations={},
    )

    with pytest.raises(KeyError, match='could not be reparsed') as exc_info:
        context.finalize_asset_push(
            asset=runtime_assets.Markdown('hello'),
            name='notes',
            title='Notes',
            description=None,
            asset_type=runtime_assets.Markdown,
        )

    assert 'Syntax error on line 10, column 13: invalid syntax.' in str(exc_info.value)
    assert 'Offending code: `broken =`.' in str(exc_info.value)


def test_runtime_context_resolves_stale_upstream_with_warning_and_hashes(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-stale',
        source_hash='consumer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'count': Binding(
                source_node='producer',
                source_artifact='value',
                data_type='int',
            )
        },
        outputs={'result': Port(name='result', data_type='int', role=ArtifactRole.OUTPUT)},
    )

    persisted = context.object_store.persist_value(42, 'int')
    context.db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='value',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code',
        upstream_data_hash='producer-data',
        run_id='upstream-run',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    context.db.set_artifact_head_state('producer', 'value', ArtifactState.STALE)

    metadata = context.resolve_pull('count')
    context.record_pull('count', metadata)
    pushed = context.finalize_value_push(name='result', value=84, data_type='int', role=ArtifactRole.OUTPUT)
    head = context.db.get_artifact_head('consumer', 'result')

    assert metadata['value'] == 42
    assert metadata['artifact_hash'] == persisted['artifact_hash']
    assert metadata['upstream_code_hash'] == 'producer-code'
    assert metadata['state'] == ArtifactState.STALE.value
    assert metadata['warnings'] == [
        {
            'code': 'stale_input',
            'message': 'Loaded stale artifact `producer/value`.',
            'artifact': 'producer/value',
        }
    ]
    assert pushed['state'] == ArtifactState.STALE.value
    assert head is not None
    assert head['state'] == ArtifactState.STALE.value
    assert head['warnings'] == metadata['warnings']
    assert head['upstream_code_hash'] == combine_hashes(['consumer-source', 'consumer/result', 'producer-code'])
    assert head['upstream_data_hash'] == combine_hashes(
        ['consumer-source', 'consumer/result', persisted['artifact_hash']]
    )


def test_runtime_context_rejects_type_mismatch_for_bound_input(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-mismatch',
        source_hash='consumer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={
            'table': Binding(
                source_node='producer',
                source_artifact='value',
                data_type='pandas.DataFrame',
            )
        },
        outputs={},
    )

    persisted = context.object_store.persist_value(42, 'int')
    context.db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='value',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code',
        upstream_data_hash='producer-data',
        run_id='upstream-run',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    try:
        context.resolve_pull('table')
    except TypeError as exc:
        assert 'expected pandas.DataFrame, got int' in str(exc)
    else:
        raise AssertionError('Expected type mismatch to raise TypeError')


def test_runtime_context_marks_output_stale_when_bound_input_becomes_stale_after_pull(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-stale',
        source_hash='consumer-source',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={
            'count': Binding(
                source_node='producer',
                source_artifact='value',
                data_type='int',
            )
        },
        outputs={'result': Port(name='result', data_type='int', role=ArtifactRole.OUTPUT)},
    )

    persisted = context.object_store.persist_value(42, 'int')
    context.db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='value',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code',
        upstream_data_hash='producer-data',
        run_id='upstream-run',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    metadata = context.resolve_pull('count')
    context.record_pull('count', metadata)
    context.db.set_artifact_head_state('producer', 'value', ArtifactState.STALE)

    pushed = context.finalize_value_push(name='result', value=84, data_type='int', role=ArtifactRole.OUTPUT)
    head = context.db.get_artifact_head('consumer', 'result')

    assert pushed['state'] == ArtifactState.STALE.value
    assert head is not None
    assert head['state'] == ArtifactState.STALE.value
    assert any(warning['code'] == 'stale_input' for warning in head['warnings'])


def test_runtime_context_marks_output_stale_when_loaded_input_is_no_longer_current_head(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-outdated',
        source_hash='consumer-source',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={
            'count': Binding(
                source_node='producer',
                source_artifact='value',
                data_type='int',
            )
        },
        outputs={'result': Port(name='result', data_type='int', role=ArtifactRole.OUTPUT)},
    )

    persisted_old = context.object_store.persist_value(42, 'int')
    context.db.upsert_artifact_object(
        persisted_old['artifact_hash'],
        persisted_old['storage_kind'],
        persisted_old['data_type'],
        persisted_old['size_bytes'],
        persisted_old.get('extension'),
        persisted_old.get('mime_type'),
        persisted_old.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='value',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted_old['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code-old',
        upstream_data_hash='producer-data-old',
        run_id='upstream-run-old',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    metadata = context.resolve_pull('count')
    context.record_pull('count', metadata)

    persisted_new = context.object_store.persist_value(84, 'int')
    context.db.upsert_artifact_object(
        persisted_new['artifact_hash'],
        persisted_new['storage_kind'],
        persisted_new['data_type'],
        persisted_new['size_bytes'],
        persisted_new.get('extension'),
        persisted_new.get('mime_type'),
        persisted_new.get('preview'),
    )
    context.db.create_artifact_version(
        node_id='producer',
        artifact_name='value',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted_new['artifact_hash'],
        source_hash='producer-source',
        upstream_code_hash='producer-code-new',
        upstream_data_hash='producer-data-new',
        run_id='upstream-run-new',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )

    pushed = context.finalize_value_push(name='result', value=126, data_type='int', role=ArtifactRole.OUTPUT)
    head = context.db.get_artifact_head('consumer', 'result')

    assert pushed['state'] == ArtifactState.STALE.value
    assert head is not None
    assert head['state'] == ArtifactState.STALE.value
    assert any(warning['code'] == 'outdated_input' for warning in head['warnings'])


def test_runtime_context_finalize_value_push_persists_dataframe_preview(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-frame',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'sample_df': Port(name='sample_df', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
    )
    frame = pd.DataFrame({'value': [1, 2, 3]})

    context.finalize_value_push(name='sample_df', value=frame, data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)
    head = context.db.get_artifact_head('producer', 'sample_df')

    assert head is not None
    assert head['state'] == ArtifactState.READY.value
    assert head['data_type'] == 'pandas.DataFrame'
    assert head['preview']['rows'] == 3
    assert head['preview']['columns'] == 1


def test_runtime_context_finalize_value_push_serializes_dataframe_datetime_preview(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-frame-datetime',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'sample_df': Port(name='sample_df', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
    )
    frame = pd.DataFrame({'created_at': [pd.Timestamp('2024-01-02T03:04:05Z')]})

    context.finalize_value_push(name='sample_df', value=frame, data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)
    head = context.db.get_artifact_head('producer', 'sample_df')

    assert head is not None
    assert head['preview']['sample'] == [{'created_at': '2024-01-02T03:04:05+00:00'}]


def test_runtime_context_finalize_asset_push_persists_markdown_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-markdown-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'notes': AssetDeclaration(
                node_id='producer',
                name='notes',
                title='Notes',
                description='Summary',
                declared_asset_type='markdown',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.Markdown('hello'),
        name='notes',
        title='Notes',
        description='Summary',
        asset_type=runtime_assets.Markdown,
    )
    head = context.db.get_asset_head('producer', 'notes')

    assert pushed['asset_name'] == 'notes'
    assert head is not None
    assert head['state'] == ArtifactState.READY.value
    assert head['asset_type'] == 'markdown'
    assert head['definition']['markdown_text'] == 'hello'
    assert head['objects'] == []


def test_runtime_context_finalize_asset_push_persists_dataframe_backing_dataset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-dataframe-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'table': AssetDeclaration(
                node_id='producer',
                name='table',
                title='Table',
                description=None,
                declared_asset_type='dataframe',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.DataFrame(pd.DataFrame({'value': [1, 2, 3]})),
        name='table',
        title='Table',
        description=None,
        asset_type=runtime_assets.DataFrame,
    )
    head = context.db.get_asset_head('producer', 'table')

    assert pushed['asset_name'] == 'table'
    assert head is not None
    assert head['asset_type'] == 'dataframe'
    assert head['definition']['row_count'] == 3
    assert head['objects'][0]['object_role'] == 'backing_dataset'


def test_runtime_context_finalize_asset_push_persists_iframe_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-iframe-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'embedded_report': AssetDeclaration(
                node_id='producer',
                name='embedded_report',
                title='Embedded report',
                description='External dashboard',
                declared_asset_type='iframe',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.Iframe('https://example.com/report'),
        name='embedded_report',
        title='Embedded report',
        description='External dashboard',
        asset_type=runtime_assets.Iframe,
    )
    head = context.db.get_asset_head('producer', 'embedded_report')

    assert pushed['asset_name'] == 'embedded_report'
    assert head is not None
    assert head['state'] == ArtifactState.READY.value
    assert head['asset_type'] == 'iframe'
    assert head['definition']['iframe_url'] == 'https://example.com/report'
    assert head['objects'] == []


def test_runtime_context_asset_head_uses_versioned_metadata_after_declaration_changes(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-versioned-asset-metadata',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'notes': AssetDeclaration(
                node_id='producer',
                name='notes',
                title='Original notes',
                description='Original description',
                declared_asset_type='markdown',
                declaration_index=0,
            )
        },
    )

    context.finalize_asset_push(
        asset=runtime_assets.Markdown('hello'),
        name='notes',
        title='Original notes',
        description='Original description',
        asset_type=runtime_assets.Markdown,
    )
    context.db.replace_asset_declarations(
        'producer',
        'updated-source-hash',
        [
            AssetDeclaration(
                node_id='producer',
                name='notes',
                title='Renamed notes',
                description='Updated description',
                declared_asset_type='markdown',
                declaration_index=0,
            )
        ],
    )

    head = context.db.get_asset_head('producer', 'notes')

    assert head is not None
    assert head['title'] == 'Original notes'
    assert head['description'] == 'Original description'
    assert head['definition']['display_title'] == 'Original notes'
    assert head['definition']['description'] == 'Original description'


def test_runtime_context_finalize_asset_push_persists_histogram_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-histogram-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'value_hist': AssetDeclaration(
                node_id='producer',
                name='value_hist',
                title='Value histogram',
                description='Distribution',
                declared_asset_type='histogram',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.Histogram(
            pd.DataFrame(
                {
                    'value': [1, 2, 2, 3],
                    'segment': ['a', 'a', 'b', 'b'],
                    'weight': [0.1, 0.3, 0.7, 0.9],
                    'palette': ['red', 'blue', 'red', 'green'],
                }
            ),
            x='value',
            bin_count=8,
        ),
        name='value_hist',
        title='Value histogram',
        description='Distribution',
        asset_type=runtime_assets.Histogram,
    )
    head = context.db.get_asset_head('producer', 'value_hist')

    assert pushed['asset_name'] == 'value_hist'
    assert head is not None
    assert head['asset_type'] == 'histogram'
    assert head['definition']['histogram_column'] == 'value'
    assert 'encodings' not in head['definition']
    assert 'histogram_shape_column' not in head['definition']
    assert 'histogram_size_column' not in head['definition']
    assert 'histogram_color_column' not in head['definition']
    assert head['default_modifiers']['bin_count'] == 8
    assert head['default_modifiers']['bar_width'] == 90
    assert head['default_modifiers']['x_axis']['label'] == 'value'
    assert head['default_modifiers']['y_axis']['label'] == 'Rows'
    assert head['default_modifiers']['title']['text'] == 'Value histogram'
    assert {entry['id'] for entry in head['modifier_schema']} >= {
        'bin_count',
        'bar_width',
        'border_thickness',
        'x_axis',
        'y_axis',
        'title',
    }
    assert head['objects'][0]['object_role'] == 'backing_dataset'


def test_runtime_context_finalize_asset_push_persists_temporal_histogram_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-temporal-histogram-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'created_hist': AssetDeclaration(
                node_id='producer',
                name='created_hist',
                title='Created histogram',
                description='Temporal distribution',
                declared_asset_type='histogram',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.Histogram(
            pd.DataFrame(
                {
                    'created_at': pd.date_range('2024-01-01', periods=4, freq='MS'),
                    'segment': ['a', 'a', 'b', 'b'],
                    'weight': [0.1, 0.3, 0.7, 0.9],
                    'palette': ['red', 'blue', 'red', 'green'],
                }
            ),
            x='created_at',
            granularity='month',
        ),
        name='created_hist',
        title='Created histogram',
        description='Temporal distribution',
        asset_type=runtime_assets.Histogram,
    )
    head = context.db.get_asset_head('producer', 'created_hist')

    assert pushed['asset_name'] == 'created_hist'
    assert head is not None
    assert head['asset_type'] == 'histogram'
    assert head['definition']['histogram_column'] == 'created_at'
    assert 'encodings' not in head['definition']
    assert 'default_granularity' not in head['definition']
    assert head['default_modifiers']['granularity'] == 'month'
    assert head['default_modifiers']['x_axis']['tick_count'] == 20
    assert {entry['id'] for entry in head['modifier_schema']} >= {
        'granularity',
        'bar_width',
        'border_thickness',
        'x_axis',
        'y_axis',
        'title',
    }
    assert head['objects'][0]['object_role'] == 'backing_dataset'


def test_runtime_context_finalize_asset_push_persists_scatter_plot_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-scatter-plot-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'xy_plot': AssetDeclaration(
                node_id='producer',
                name='xy_plot',
                title='XY plot',
                description='Relationship view',
                declared_asset_type='scatter_plot',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.ScatterPlot(
            pd.DataFrame(
                {
                    'x': [1, 2, 3],
                    'y': [4, 5, 6],
                    'name': ['one', 'two', 'three'],
                    'group': ['a', 'b', 'a'],
                    'weight': [10, 20, 30],
                    'palette': ['red', 'blue', 'green'],
                }
            ),
            x='x',
            y='y',
            label='name',
            shape='group',
            size='weight',
            color='palette',
            size_scaling=1.8,
            min_point_size=24,
            max_point_size=160,
            show_legend=False,
            shape_style='filled',
            x_axis={
                'label_size': 16,
                'label': 'Custom x',
                'hide_label': False,
                'tick_count': 5,
                'tick_size': 8,
                'show_grid_lines': False,
                'scale': 'lin',
            },
            y_axis={
                'label_size': 15,
                'label': 'Custom y',
                'hide_label': False,
                'tick_count': None,
                'tick_size': None,
                'show_grid_lines': True,
                'scale': 'log',
            },
            title={'size': 20, 'text': 'Custom scatter', 'hide_title': False, 'position': 'bottom'},
        ),
        name='xy_plot',
        title='XY plot',
        description='Relationship view',
        asset_type=runtime_assets.ScatterPlot,
    )
    head = context.db.get_asset_head('producer', 'xy_plot')

    assert pushed['asset_name'] == 'xy_plot'
    assert head is not None
    assert head['asset_type'] == 'scatter_plot'
    assert head['definition']['scatter_x_column'] == 'x'
    assert head['definition']['scatter_y_column'] == 'y'
    assert head['definition']['scatter_label_column'] == 'name'
    assert head['definition']['scatter_shape_column'] == 'group'
    assert head['definition']['scatter_size_column'] == 'weight'
    assert head['definition']['scatter_color_column'] == 'palette'
    assert 'encodings' not in head['definition']
    assert head['default_modifiers']['min_point_size'] == 24
    assert head['default_modifiers']['max_point_size'] == 160
    assert head['default_modifiers']['size_scaling'] == 1.8
    assert head['default_modifiers']['show_legend'] is False
    assert head['default_modifiers']['shape_style'] == 'filled'
    assert head['default_modifiers']['x_axis']['label'] == 'Custom x'
    assert head['default_modifiers']['y_axis']['scale'] == 'log'
    assert head['default_modifiers']['title']['position'] == 'bottom'
    assert head['objects'][0]['object_role'] == 'backing_dataset'
    assert {entry['id'] for entry in head['modifier_schema']} >= {
        'min_point_size',
        'max_point_size',
        'size_scaling',
        'show_legend',
        'shape_style',
        'x_axis',
        'y_axis',
        'title',
    }


def test_scatter_plot_rejects_dataframes_above_max_point_limit() -> None:
    frame = pd.DataFrame(
        {
            'x': list(range(MAX_SCATTER_PLOT_POINTS + 1)),
            'y': list(range(MAX_SCATTER_PLOT_POINTS + 1)),
        }
    )

    with pytest.raises(
        ValueError,
        match=rf'Scatter plot assets support at most {MAX_SCATTER_PLOT_POINTS:,} rows; received {MAX_SCATTER_PLOT_POINTS + 1:,}\.',
    ):
        runtime_assets.ScatterPlot(frame, x='x', y='y')


def test_runtime_context_finalize_asset_push_persists_pie_chart_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-pie-chart-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'category_share': AssetDeclaration(
                node_id='producer',
                name='category_share',
                title='Category share',
                description='Category distribution',
                declared_asset_type='pie_chart',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.PieChart(
            pd.DataFrame(
                {
                    'segment': ['a', 'a', 'b', 'c'],
                    'tone': ['#ff0000', '#ff0000', '#00ff00', '#0000ff'],
                    'value': [1, 2, 3, 4],
                }
            ),
            category='segment',
            color='tone',
            inner_radius=0.35,
            label_size=14,
            label_threshold=7,
            label_position=130,
            merge_threshold=4,
            border_thickness=2,
            category_order=['c', 'a'],
            merged_category_label='Remainder',
            show_merged_category=False,
            show_percentages=True,
            title={'size': 18, 'text': 'Custom pie', 'hide_title': False, 'position': 'bottom'},
        ),
        name='category_share',
        title='Category share',
        description='Category distribution',
        asset_type=runtime_assets.PieChart,
    )
    head = context.db.get_asset_head('producer', 'category_share')

    assert pushed['asset_name'] == 'category_share'
    assert head is not None
    assert head['asset_type'] == 'pie_chart'
    assert head['definition']['pie_category_column'] == 'segment'
    assert 'encodings' not in head['definition']
    assert 'pie_color_column' not in head['definition']
    assert head['definition']['pie_color_mapping'] == [
        {'value': 'a', 'color': '#ff0000'},
        {'value': 'b', 'color': '#00ff00'},
        {'value': 'c', 'color': '#0000ff'},
    ]
    assert head['default_modifiers']['inner_radius'] == 0.35
    assert head['default_modifiers']['label_size'] == 14
    assert head['default_modifiers']['label_threshold'] == 7
    assert head['default_modifiers']['label_position'] == 130
    assert head['default_modifiers']['merge_threshold'] == 4
    assert head['default_modifiers']['border_thickness'] == 2
    assert head['default_modifiers']['category_order'] == ['c', 'a']
    assert head['default_modifiers']['merged_category_label'] == 'Remainder'
    assert head['default_modifiers']['show_merged_category'] is False
    assert head['default_modifiers']['show_percentages'] is True
    assert head['default_modifiers']['title']['position'] == 'bottom'
    assert {entry['id'] for entry in head['modifier_schema']} >= {
        'inner_radius',
        'label_size',
        'label_threshold',
        'label_position',
        'merge_threshold',
        'border_thickness',
        'category_order',
        'merged_category_label',
        'show_merged_category',
        'show_percentages',
        'title',
    }
    assert head['objects'][0]['object_role'] == 'backing_dataset'


def test_runtime_context_finalize_asset_push_persists_bar_chart_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-bar-chart-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'category_totals': AssetDeclaration(
                node_id='producer',
                name='category_totals',
                title='Category totals',
                description='Category totals description',
                declared_asset_type='bar_chart',
                declaration_index=0,
            )
        },
    )

    pushed = context.finalize_asset_push(
        asset=runtime_assets.BarChart(
            pd.DataFrame(
                {
                    'segment': ['a', 'a', 'b', 'c'],
                    'tone': ['#ff0000', '#ff0000', '#00ff00', '#0000ff'],
                    'value': [1, 2, 3, 4],
                }
            ),
            category='segment',
            color='tone',
            value='value',
            aggregation='mean',
            bar_width=75,
            border_thickness=1.5,
            category_order='value_asc',
            x_axis={
                'label_size': 11,
                'label': 'Segment',
                'hide_label': False,
                'tick_count': None,
                'tick_size': None,
                'show_grid_lines': False,
                'scale': 'lin',
            },
            y_axis={
                'label_size': 12,
                'label': 'Average value',
                'hide_label': False,
                'tick_count': 5,
                'tick_size': 4,
                'show_grid_lines': True,
                'scale': 'lin',
            },
            title={'size': 18, 'text': 'Custom bar', 'hide_title': False, 'position': 'bottom'},
        ),
        name='category_totals',
        title='Category totals',
        description='Category totals description',
        asset_type=runtime_assets.BarChart,
    )
    head = context.db.get_asset_head('producer', 'category_totals')

    assert pushed['asset_name'] == 'category_totals'
    assert head is not None
    assert head['asset_type'] == 'bar_chart'
    assert head['definition']['bar_category_column'] == 'segment'
    assert head['definition']['bar_value_column'] == 'value'
    assert head['definition']['bar_aggregation'] == 'mean'
    assert 'encodings' not in head['definition']
    assert 'bar_color_column' not in head['definition']
    assert head['definition']['bar_color_mapping'] == [
        {'value': 'a', 'color': '#ff0000'},
        {'value': 'b', 'color': '#00ff00'},
        {'value': 'c', 'color': '#0000ff'},
    ]
    assert head['default_modifiers']['bar_width'] == 75
    assert head['default_modifiers']['border_thickness'] == 1.5
    assert head['default_modifiers']['category_order'] == 'value_asc'
    assert head['default_modifiers']['x_axis']['label'] == 'Segment'
    assert head['default_modifiers']['y_axis']['label'] == 'Average value'
    assert head['default_modifiers']['title']['position'] == 'bottom'
    assert {entry['id'] for entry in head['modifier_schema']} >= {
        'bar_width',
        'border_thickness',
        'category_order',
        'x_axis',
        'y_axis',
        'title',
    }
    assert head['objects'][0]['object_role'] == 'backing_dataset'


def test_runtime_context_finalize_asset_push_persists_collection_asset(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-collection-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'notes_collection': AssetDeclaration(
                node_id='producer',
                name='notes_collection',
                title='Notes collection',
                description='Grouped assets',
                declared_asset_type='collection',
                declaration_index=0,
            )
        },
    )
    collection = runtime_assets.Collection(display_mode='single')
    collection.add_asset(runtime_assets.Markdown('hello'))
    collection.add_asset(runtime_assets.Iframe('https://example.com/embed'), name='report', title='Embedded report')

    pushed = context.finalize_asset_push(
        asset=collection,
        name='notes_collection',
        title='Notes collection',
        description='Grouped assets',
        asset_type=runtime_assets.Collection,
    )
    head = context.db.get_asset_head('producer', 'notes_collection')

    assert pushed['asset_name'] == 'notes_collection'
    assert head is not None
    assert head['asset_type'] == 'collection'
    assert head['definition']['display_mode_default'] == 'single'
    assert [child['name'] for child in head['definition']['children']] == ['asset_1', 'report']
    assert [child['title'] for child in head['definition']['children']] == ['Asset 1', 'Embedded report']
    assert head['definition']['children'][0]['asset_type'] == 'markdown'
    assert head['definition']['children'][0]['markdown_text'] == 'hello'
    assert head['definition']['children'][1]['asset_type'] == 'iframe'
    assert head['definition']['children'][1]['iframe_url'] == 'https://example.com/embed'
    assert head['objects'] == []


def test_runtime_context_finalize_asset_push_remaps_collection_child_object_indexes(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-collection-dataframe-asset',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'table_collection': AssetDeclaration(
                node_id='producer',
                name='table_collection',
                title='Table collection',
                description=None,
                declared_asset_type='collection',
                declaration_index=0,
            )
        },
    )
    collection = runtime_assets.Collection()
    collection.add_asset(runtime_assets.DataFrame(pd.DataFrame({'left': [1, 2]})), name='left_table')
    collection.add_asset(runtime_assets.DataFrame(pd.DataFrame({'right': [3, 4]})), name='right_table')

    context.finalize_asset_push(
        asset=collection,
        name='table_collection',
        title='Table collection',
        description=None,
        asset_type=runtime_assets.Collection,
    )
    head = context.db.get_asset_head('producer', 'table_collection')

    assert head is not None
    assert head['definition']['display_mode_default'] == 'single'
    assert [item['object_index'] for item in head['objects']] == [0, 1]
    assert [child['objects'][0]['object_index'] for child in head['definition']['children']] == [0, 1]
    assert [child['objects'][0]['object_role'] for child in head['definition']['children']] == [
        'backing_dataset',
        'backing_dataset',
    ]


def test_runtime_context_rejects_output_not_declared_in_interface(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-undeclared',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
    )

    try:
        context.finalize_value_push(name='missing', value=1, data_type='int', role=ArtifactRole.OUTPUT)
    except KeyError as exc:
        assert 'missing' in str(exc)
    else:
        raise AssertionError('Expected undeclared output to raise KeyError')


def test_runtime_context_rejects_output_type_mismatch(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-type-mismatch',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'result': Port(name='result', data_type='int', role=ArtifactRole.OUTPUT)},
    )

    try:
        context.finalize_value_push(name='result', value='oops', data_type='str', role=ArtifactRole.OUTPUT)
    except TypeError as exc:
        assert 'expected int, got str' in str(exc)
    else:
        raise AssertionError('Expected output type mismatch to raise TypeError')


def test_runtime_context_refreshes_interactive_output_contracts_from_notebook(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'producer.py'
    notebook_path.write_text(
        """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    value = 1
    artifacts.push(value, name='fresh_output', data_type=int)
    return
""",
        encoding='utf-8',
    )
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-interactive-refresh',
        source_hash='stale-source-hash',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={},
        outputs={'old_output': Port(name='old_output', data_type='int', role=ArtifactRole.OUTPUT)},
    )

    pushed = context.finalize_value_push(name='fresh_output', value=1, data_type='int', role=ArtifactRole.OUTPUT)

    assert pushed['artifact_name'] == 'fresh_output'
    assert 'fresh_output' in context.outputs
    assert 'old_output' not in context.outputs
    assert context.source_hash != 'stale-source-hash'


def test_runtime_context_refreshes_interactive_bindings_from_live_graph(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    notebook_path = project_root / 'notebooks' / 'consumer.py'
    notebook_path.write_text(
        """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    incoming = artifacts.pull(name='incoming', data_type=int)
    return incoming
""",
        encoding='utf-8',
    )
    graph_dir = project_root / 'graph'
    (graph_dir / 'meta.json').write_text(
        '{\n  "schema_version": 1,\n  "project_id": "project",\n  "graph_version": 2,\n  "updated_at": "2026-03-26T00:00:00Z"\n}\n',
        encoding='utf-8',
    )
    (graph_dir / 'nodes.json').write_text(
        '[\n  {"id": "producer", "kind": "notebook", "title": "Producer", "path": "notebooks/producer.py", "template": null, "ui": {}},\n  {"id": "consumer", "kind": "notebook", "title": "Consumer", "path": "notebooks/consumer.py", "template": null, "ui": {}}\n]\n',
        encoding='utf-8',
    )
    (graph_dir / 'edges.json').write_text(
        '[\n  {"id": "edge-1", "source_node": "producer", "source_port": "value", "target_node": "consumer", "target_port": "incoming"}\n]\n',
        encoding='utf-8',
    )
    context = RuntimeContext(
        project_root=project_root,
        node_id='consumer',
        run_id='run-interactive-bindings',
        source_hash='stale-source-hash',
        lineage_mode=LineageMode.INTERACTIVE_HEURISTIC,
        bindings={},
        outputs={},
    )

    try:
        context.validate_pull_contract(name='incoming', data_type='int')
    except Exception as exc:
        raise AssertionError(f'Expected live binding refresh to succeed, got: {exc}') from exc

    binding = context.bindings['incoming']
    assert binding.source_node == 'producer'
    assert binding.source_artifact == 'value'


def test_current_runtime_context_loads_project_id_from_project_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    token = _RUNTIME_CONTEXT.set(None)
    monkeypatch.setenv('BULLETJOURNAL_PROJECT_ROOT', str(project_root))
    monkeypatch.setenv('BULLETJOURNAL_NODE_ID', 'consumer')
    monkeypatch.setenv('BULLETJOURNAL_RUN_ID', 'run-env')
    monkeypatch.setenv('BULLETJOURNAL_SOURCE_HASH', 'source-hash')
    monkeypatch.setenv('BULLETJOURNAL_LINEAGE_MODE', LineageMode.MANAGED.value)
    monkeypatch.delenv('BULLETJOURNAL_BINDINGS_JSON', raising=False)
    monkeypatch.delenv('BULLETJOURNAL_OUTPUTS_JSON', raising=False)

    try:
        context = current_runtime_context()
    finally:
        _RUNTIME_CONTEXT.reset(token)

    assert context.node_id == 'consumer'
    assert context.project_id == 'project'
