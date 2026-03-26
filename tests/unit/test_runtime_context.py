import pandas as pd
import pytest

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode
from bulletjournal.domain.hashing import combine_hashes, hash_json
from bulletjournal.domain.models import Port
from bulletjournal.runtime.context import _RUNTIME_CONTEXT, Binding, RuntimeContext, current_runtime_context
from bulletjournal.storage.project_fs import init_project_root


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
    artifacts.push(value, name='fresh_output', data_type=int, is_output=True)
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
        '[\n  {"id": "producer", "kind": "notebook", "title": "Producer", "path": "notebooks/producer.py", "template": null, "ui": {"hidden_inputs": []}},\n  {"id": "consumer", "kind": "notebook", "title": "Consumer", "path": "notebooks/consumer.py", "template": null, "ui": {"hidden_inputs": []}}\n]\n',
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
    except Exception as exc:  # noqa: BLE001
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
