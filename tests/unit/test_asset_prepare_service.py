from __future__ import annotations

import io
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bulletjournal.runtime.assets as runtime_assets
from bulletjournal.api.routes import artifacts, assets
from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.domain.errors import InvalidRequestError
from bulletjournal.domain.models import AssetDeclaration, Port
from bulletjournal.runtime.context import RuntimeContext
from bulletjournal.services.asset_prepare_service import AssetPrepareService
from bulletjournal.storage.project_fs import init_project_root


class _FakeProjectService:
    def __init__(self, *, project) -> None:
        self._project = project

    def get_node(self, node_id: str):
        return {'id': node_id}

    def require_project(self):
        return self._project


def test_asset_prepare_service_prepares_interactive_collection_child(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-collection-prepare',
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
    collection = runtime_assets.Collection(display_mode='single')
    collection.add_asset(runtime_assets.DataFrame(pd.DataFrame({'left': [1, 2, 3]})), name='left_table')
    collection.add_asset(runtime_assets.Markdown('hello'), name='notes')
    context.finalize_asset_push(
        asset=collection,
        name='table_collection',
        title='Table collection',
        description=None,
        asset_type=runtime_assets.Collection,
    )

    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )
    response = service.prepare_asset(
        'producer',
        'table_collection',
        asset_version_id=None,
        modifier_overrides={'page': {'index': 0, 'size': 25}, 'max_text_lines': 3},
        transient_modifiers={},
        panel_context={'collection_child_name': 'left_table'},
        persisted_override_schema_hash=None,
    )

    assert response['payloads']['table']['kind'] == 'table'
    assert response['payloads']['table']['rows_total'] == 3
    assert response['override_schema_hash'] is not None
    assert response['resolved_modifiers']['page'] == {'index': 0, 'size': 25}
    assert response['resolved_modifiers']['max_text_lines'] == 3


def test_asset_prepare_service_accepts_valid_persisted_overrides_after_schema_change(tmp_path) -> None:
    context = _dataframe_asset_context(tmp_path)
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    response = service.prepare_asset(
        'producer',
        'table',
        asset_version_id=None,
        modifier_overrides={'page': {'index': 0, 'size': 25}, 'max_text_lines': 4},
        transient_modifiers={},
        panel_context=None,
        persisted_override_schema_hash='outdated-schema-hash',
    )

    assert response['errors'] == []
    assert response['payloads']['table']['kind'] == 'table'
    assert response['resolved_modifiers']['page'] == {'index': 0, 'size': 25}
    assert response['resolved_modifiers']['max_text_lines'] == 4


@pytest.mark.parametrize('lines', [0, 11, 1.5, True, None])
def test_asset_prepare_service_rejects_invalid_text_lines_overrides(tmp_path, lines) -> None:
    context = _dataframe_asset_context(tmp_path)
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )
    for schema_hash in [None, 'outdated-schema-hash']:
        kwargs = {
            'asset_version_id': None,
            'modifier_overrides': {'max_text_lines': lines},
            'transient_modifiers': {},
            'panel_context': None,
            'persisted_override_schema_hash': schema_hash,
        }
        if schema_hash is None:
            with pytest.raises(InvalidRequestError, match='max_text_lines'):
                service.prepare_asset('producer', 'table', **kwargs)
        else:
            response = service.prepare_asset('producer', 'table', **kwargs)
            assert response['errors'][0]['code'] == 'override_incompatible'


def test_asset_prepare_service_marks_invalid_persisted_overrides_incompatible(tmp_path) -> None:
    context = _dataframe_asset_context(tmp_path)
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    response = service.prepare_asset(
        'producer',
        'table',
        asset_version_id=None,
        modifier_overrides={'sort': [{'column': 'missing_column', 'direction': 'asc'}]},
        transient_modifiers={},
        panel_context=None,
        persisted_override_schema_hash='outdated-schema-hash',
    )

    assert response['payloads'] == {}
    assert response['resolved_modifiers'] == {}
    assert response['errors'][0]['code'] == 'override_incompatible'
    assert 'missing_column' in response['errors'][0]['message']


def test_asset_prepare_service_still_rejects_invalid_overrides_without_schema_mismatch(tmp_path) -> None:
    context = _dataframe_asset_context(tmp_path)
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    with pytest.raises(InvalidRequestError, match='missing_column'):
        service.prepare_asset(
            'producer',
            'table',
            asset_version_id=None,
            modifier_overrides={'sort': [{'column': 'missing_column', 'direction': 'asc'}]},
            transient_modifiers={},
            panel_context=None,
            persisted_override_schema_hash=None,
        )


def test_asset_prepare_service_prepares_dataframe_artifact(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-artifact-prepare',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'table': Port(name='table', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
    )
    context.finalize_value_push(
        name='table',
        value=pd.DataFrame({'value': [3, 1, 2], 'label': ['three', 'one', 'two']}),
        data_type='pandas.DataFrame',
        role=ArtifactRole.OUTPUT,
    )
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    response = service.prepare_artifact_dataframe(
        'producer',
        'table',
        artifact_version_id=None,
        modifier_overrides={
            'sort': [{'column': 'value', 'direction': 'asc'}],
            'max_text_lines': 5,
            'filters': [{'kind': 'range', 'column': 'value', 'lower': 2}],
            'highlights': [
                {
                    'kind': 'range',
                    'column': 'value',
                    'lower': 3,
                    'highlight_color': '#ff0000',
                    'highlight_scope': 'cell',
                }
            ],
        },
        transient_modifiers={},
    )

    assert response['payloads']['table']['rows_total'] == 2
    assert [row['value'] for row in response['payloads']['table']['rows']] == [2, 3]
    assert response['resolved_modifiers']['sort'] == [{'column': 'value', 'direction': 'asc'}]
    assert response['resolved_modifiers']['filters'][0]['column'] == 'value'
    assert response['resolved_modifiers']['highlights'][0]['highlight_color'] == '#ff0000'
    assert response['resolved_modifiers']['max_text_lines'] == 5
    refreshed = service.prepare_artifact_dataframe(
        'producer', 'table', artifact_version_id=None, modifier_overrides={}, transient_modifiers={}
    )
    assert refreshed['resolved_modifiers']['max_text_lines'] == 1


def test_asset_prepare_service_defaults_dataframe_artifact_preview_to_ten_rows(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-artifact-preview-page-size',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'table': Port(name='table', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
    )
    context.finalize_value_push(
        name='table',
        value=pd.DataFrame({'value': list(range(12))}),
        data_type='pandas.DataFrame',
        role=ArtifactRole.OUTPUT,
    )
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    response = service.prepare_artifact_dataframe(
        'producer',
        'table',
        artifact_version_id=None,
        modifier_overrides={},
        transient_modifiers={},
    )

    assert response['resolved_modifiers']['page'] == {'index': 0, 'size': 10}
    assert response['payloads']['table']['rows_total'] == 12
    assert len(response['payloads']['table']['rows']) == 10


def test_asset_prepare_service_prepares_empty_dataframe_artifact(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-empty-artifact-prepare',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'table': Port(name='table', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
    )
    context.finalize_value_push(
        name='table',
        value=None,
        data_type='pandas.DataFrame',
        role=ArtifactRole.OUTPUT,
    )
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )

    response = service.prepare_artifact_dataframe(
        'producer',
        'table',
        artifact_version_id=None,
        modifier_overrides={},
        transient_modifiers={},
    )

    assert response['payloads']['table']['rows_total'] == 0
    assert response['payloads']['table']['columns'] == []
    assert response['payloads']['table']['rows'] == []
    downloaded = pd.read_parquet(
        io.BytesIO(
            service.download_dataframe_preview(
                'producer', 'table', version_id=None, modifier_overrides={}, artifact=True
            )
        )
    )
    assert downloaded.empty
    assert list(downloaded.columns) == []


def _dataframe_asset_context(tmp_path) -> RuntimeContext:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-dataframe-prepare',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'table': AssetDeclaration(
                node_id='producer',
                name='table',
                title='Table',
                description='Rows',
                declared_asset_type='dataframe',
                declaration_index=0,
            )
        },
    )
    context.finalize_asset_push(
        asset=runtime_assets.DataFrame(pd.DataFrame({'value': [1, 2, 3]})),
        name='table',
        title='Table',
        description='Rows',
        asset_type=runtime_assets.DataFrame,
    )
    return context


@pytest.mark.parametrize('source', ['asset', 'collection', 'artifact'])
def test_parquet_preview_download_matches_filtered_sorted_page_and_preserves_types(tmp_path, source) -> None:
    context = _dataframe_asset_context(tmp_path)
    frame = pd.DataFrame(
        {
            'value': [3, 1, 2, 4, *range(5, 15)],
            'timestamp': pd.date_range('2026-01-01', periods=14, tz='UTC'),
            'nullable': pd.Series([None] * 14, dtype='Int64'),
        }
    )
    panel_context = None
    if source == 'artifact':
        context.outputs['table'] = Port(name='table', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)
        context.finalize_value_push(name='table', value=frame, data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)
        path = '/api/v1/artifacts/producer/table/preview/download'
    else:
        asset = runtime_assets.DataFrame(frame)
        if source == 'collection':
            context.asset_declarations['table'].declared_asset_type = 'collection'
            collection = runtime_assets.Collection()
            collection.add_asset(asset, name='child')
            asset = collection
            panel_context = {'collection_child_name': 'child'}
        context.finalize_asset_push(
            asset=asset, name='table', title='Table', description='Rows', asset_type=type(asset)
        )
        path = '/api/v1/assets/producer/table/download'
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )
    app = FastAPI()
    app.state.container = SimpleNamespace(asset_prepare_service=service)
    app.include_router(assets.router, prefix='/api/v1')
    app.include_router(artifacts.router, prefix='/api/v1')
    client = TestClient(app)
    response = client.post(
        path,
        json={
            'panel_context': panel_context,
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'value', 'direction': 'asc'}],
                'filters': [{'column': 'value', 'kind': 'range', 'lower': 2, 'upper': 3}],
            },
        },
    )
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/vnd.apache.parquet'
    downloaded = pd.read_parquet(io.BytesIO(response.content))
    pd.testing.assert_frame_equal(downloaded, frame.iloc[[2, 0]].reset_index(drop=True))
    response = client.post(
        path, json={'panel_context': panel_context, 'modifier_overrides': {'page': {'index': 1, 'size': 10}}}
    )
    pd.testing.assert_frame_equal(pd.read_parquet(io.BytesIO(response.content)), frame.iloc[10:].reset_index(drop=True))
    response = client.post(
        path, json={'panel_context': panel_context, 'modifier_overrides': {'page': {'index': 2, 'size': 10}}}
    )
    empty_page = pd.read_parquet(io.BytesIO(response.content))
    assert empty_page.empty
    assert empty_page.dtypes.equals(downloaded.dtypes)


def test_parquet_preview_download_rejects_stale_version_and_releases_lease_on_failure(tmp_path, monkeypatch) -> None:
    context = _dataframe_asset_context(tmp_path)
    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )
    with pytest.raises(InvalidRequestError, match='changed'):
        service.download_dataframe_preview('producer', 'table', version_id=-1, modifier_overrides={})
    released = []
    original_release = context.db.release_object_lease

    def release(lease_id):
        released.append(lease_id)
        original_release(lease_id)

    monkeypatch.setattr(context.db, 'release_object_lease', release)
    with pytest.raises(InvalidRequestError, match='missing'):
        service.download_dataframe_preview(
            'producer',
            'table',
            version_id=None,
            modifier_overrides={'sort': [{'column': 'missing', 'direction': 'asc'}]},
        )
    assert len(released) == 1
