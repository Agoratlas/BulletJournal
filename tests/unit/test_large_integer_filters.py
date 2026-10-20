import json
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bulletjournal.api.routes import artifacts, assets
from bulletjournal.assets.registry import serialize_asset
from bulletjournal.assets.types.bar_chart import BarChart
from bulletjournal.assets.types.collection import Collection
from bulletjournal.assets.types.dataframe import DataFrame, prepare_dataframe
from bulletjournal.assets.types.histogram import Histogram
from bulletjournal.assets.types.pie_chart import PieChart
from bulletjournal.assets.types.scatter_plot import ScatterPlot
from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.domain.models import AssetDeclaration, Port
from bulletjournal.runtime.context import RuntimeContext
from bulletjournal.services.asset_prepare_service import AssetPrepareService
from bulletjournal.services.asset_service import AssetService
from bulletjournal.storage.project_fs import init_project_root


@pytest.mark.parametrize('value', [2103982719223071223, -2103982719223071223, 2**64 - 2])
@pytest.mark.parametrize('kind', ['value', 'range'])
@pytest.mark.parametrize('modifier', ['filters', 'highlights'])
def test_large_integer_predicates_match_exactly_and_round_trip(tmp_path, value, kind, modifier) -> None:
    dtype = pl.UInt64 if value > 2**63 - 1 else pl.Int64
    path = tmp_path / 'table.parquet'
    pl.DataFrame({'id': pl.Series([value - 1, value, value + 1, None], dtype=dtype)}).write_parquet(path)
    predicate = {'column': 'id', 'kind': kind}
    if kind == 'value':
        predicate.update(values=[str(value)], include_null=False)
    else:
        predicate.update(lower=str(value), upper=str(value))
    if modifier == 'highlights':
        predicate.update(highlight_scope='cell', highlight_color='#ff0000')
    overrides = {modifier: [predicate]}

    for _ in range(2):
        payloads, resolved = prepare_dataframe(
            dataset_path=path,
            definition={},
            default_modifiers={},
            modifier_overrides=overrides,
            transient_modifiers={},
        )
        table = payloads['table']
        if modifier == 'filters':
            assert table['rows'] == [{'id': str(value)}]
        else:
            assert table['rows_total'] == 4
            assert table['cell_highlights'] == [{'row': 1, 'column': 'id', 'color': '#ff0000'}]
        overrides = json.loads(json.dumps(resolved))
        actual = overrides[modifier][0]
        if kind == 'value':
            assert actual['values'] == [str(value)]
        else:
            assert actual['lower'] == actual['upper'] == str(value)


@pytest.mark.parametrize('kind', ['value', 'range'])
@pytest.mark.parametrize('source', ['dataframe', 'bar', 'pie', 'histogram', 'scatter', 'collection'])
def test_published_highlight_defaults_preserve_large_integers(source, kind) -> None:
    value = 2103982719223071223
    frame = pd.DataFrame({'id': pd.Series([value, value + 1], dtype='Int64'), 'value': [1, 2]})
    rule = {'kind': kind, 'column': 'id', 'highlight_color': '#ff0000', 'highlight_scope': 'cell'}
    if kind == 'value':
        rule['values'] = [value]
    else:
        rule.update(lower=value, upper=value)
    factories = {
        'dataframe': lambda: DataFrame(frame, highlights=[rule]),
        'bar': lambda: BarChart(frame, category='id', value='value', highlights=[rule]),
        'pie': lambda: PieChart(frame, category='id', highlights=[rule]),
        'histogram': lambda: Histogram(frame, x='id', highlights=[rule]),
        'scatter': lambda: ScatterPlot(frame, x='id', y='value', highlights=[rule]),
        'collection': Collection,
    }
    asset = factories[source]()
    if source == 'collection':
        asset = Collection()
        asset.add_asset(DataFrame(frame, highlights=[rule]), name='child')
    serialized = serialize_asset(
        asset,
        object_store=SimpleNamespace(persist_value=lambda *_: {'artifact_hash': 'dataset'}),
        title='Asset',
        description=None,
    )
    if source == 'collection':
        child = serialized.definition['children'][0]
        modifiers = child['default_modifiers']
        schema = child['modifier_schema']
    else:
        modifiers = serialized.default_modifiers
        schema = serialized.modifier_schema
    expected = {**rule}
    if kind == 'value':
        expected['values'] = [str(value)]
    else:
        expected.update(lower=str(value), upper=str(value))
    assert modifiers['highlights'] == [expected]
    assert next(entry for entry in schema if entry['id'] == 'highlights')['default_value'] == [expected]
    assert rule.get('lower', rule.get('values', [None])[0]) == value


@pytest.mark.parametrize('source', ['asset', 'collection', 'artifact'])
def test_large_integer_filters_through_api_and_legacy_defaults(tmp_path, source) -> None:
    value = 2103982719223071223
    rule = {
        'column': 'id',
        'kind': 'value',
        'values': [value],
        'highlight_color': '#ff0000',
        'highlight_scope': 'cell',
    }
    context = RuntimeContext(
        project_root=init_project_root(tmp_path / 'project').root,
        node_id='producer',
        run_id='integer-filters',
        source_hash='source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'table': Port(name='table', data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)},
        asset_declarations={
            'table': AssetDeclaration(
                node_id='producer',
                name='table',
                title='Table',
                description=None,
                declared_asset_type='collection' if source == 'collection' else 'dataframe',
                declaration_index=0,
            )
        },
    )
    frame = pd.DataFrame({'id': pd.Series([value - 1, value, value + 1], dtype='Int64')})
    panel_context = None
    if source == 'artifact':
        context.finalize_value_push(name='table', value=frame, data_type='pandas.DataFrame', role=ArtifactRole.OUTPUT)
        endpoint = '/api/v1/artifacts/producer/table/prepare'
    else:
        asset = DataFrame(frame, highlights=[rule])
        if source == 'collection':
            asset = Collection()
            asset.add_asset(DataFrame(frame, highlights=[rule]), name='child')
            panel_context = {'collection_child_name': 'child'}
        context.finalize_asset_push(asset=asset, name='table', title='Table', description=None, asset_type=type(asset))
        # Model a version published before browser-safe integer serialization.
        with context.db._connection() as connection:
            row = connection.execute('SELECT * FROM asset_versions').fetchone()
            definition = json.loads(row['definition_json'])
            modifiers = json.loads(row['default_modifiers_json'])
            schema = json.loads(row['modifier_schema_json'])
            if source == 'collection':
                modifiers = definition['children'][0]['default_modifiers']
                schema = definition['children'][0]['modifier_schema']
            modifiers['highlights'] = [rule]
            next(entry for entry in schema if entry['id'] == 'highlights')['default_value'] = [rule]
            connection.execute(
                'UPDATE asset_versions SET definition_json = ?, default_modifiers_json = ?, modifier_schema_json = ?',
                (
                    json.dumps(definition),
                    json.dumps(modifiers if source == 'asset' else {}),
                    json.dumps(schema if source == 'asset' else []),
                ),
            )
            connection.commit()
        endpoint = '/api/v1/assets/producer/table/prepare'
    project = SimpleNamespace(state_db=context.db, object_store=context.object_store)
    project_service = SimpleNamespace(get_node=lambda _: {}, require_project=lambda: project)
    app = FastAPI()
    app.state.container = SimpleNamespace(
        asset_prepare_service=AssetPrepareService(project_service),
        asset_service=AssetService(project_service),
    )
    app.include_router(assets.router, prefix='/api/v1')
    app.include_router(artifacts.router, prefix='/api/v1')
    with TestClient(app) as client:
        if source != 'artifact':
            for path in ['/api/v1/assets/producer/table', '/api/v1/nodes/producer/assets']:
                head = client.get(path).json()
                if isinstance(head, list):
                    head = head[0]
                if source == 'collection':
                    head = head['definition']['children'][0]
                assert head['default_modifiers']['highlights'][0]['values'] == [str(value)]
                highlight_schema = next(entry for entry in head['modifier_schema'] if entry['id'] == 'highlights')
                assert highlight_schema['default_value'][0]['values'] == [str(value)]
            response = client.post(endpoint, json={'panel_context': panel_context})
            assert response.status_code == 200, response.text
            assert response.json()['payloads']['table']['cell_highlights'] == [
                {'row': 1, 'column': 'id', 'color': '#ff0000'}
            ]
        overrides = {
            'filters': [{'column': 'id', 'kind': 'value', 'values': [str(value)]}],
            'highlights': [{**rule, 'values': [str(value)]}],
        }
        for kind in ['value', 'range']:
            if kind == 'range':
                overrides = {
                    'filters': [{'column': 'id', 'kind': 'range', 'lower': str(value), 'upper': str(value)}],
                    'highlights': [
                        {key: entry for key, entry in rule.items() if key != 'values'}
                        | {'kind': 'range', 'lower': str(value), 'upper': str(value)}
                    ],
                }
            for _ in range(2):
                response = client.post(endpoint, json={'panel_context': panel_context, 'modifier_overrides': overrides})
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload['payloads']['table']['rows'] == [{'id': str(value)}]
                assert payload['payloads']['table']['cell_highlights'] == [
                    {'row': 0, 'column': 'id', 'color': '#ff0000'}
                ]
                overrides = payload['resolved_modifiers']
                if kind == 'value':
                    assert overrides['filters'][0]['values'] == [str(value)]
                else:
                    assert overrides['filters'][0]['lower'] == overrides['filters'][0]['upper'] == str(value)
