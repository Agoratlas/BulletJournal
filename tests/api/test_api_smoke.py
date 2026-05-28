import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


def test_open_and_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    response = client.get('/api/v1/project/snapshot')
    assert response.status_code == 200

    project_id = response.json()['project']['project_id']
    snapshot = client.get('/api/v1/project/snapshot')
    assert snapshot.status_code == 200
    assert snapshot.json()['project']['project_id'] == project_id
    assert 'notices' in snapshot.json()


def test_node_detail_endpoint_available_at_project_nodes_path(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    detail = client.get('/api/v1/nodes/sample_node')

    assert detail.status_code == 200
    assert detail.json()['id'] == 'sample_node'


def test_new_notebook_is_custom_and_uses_empty_template_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'custom_node',
                    'title': 'Custom Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'custom_node')

    assert node['template'] is None

    notebook = client.get('/api/v1/nodes/custom_node/notebook/download')
    assert notebook.status_code == 200
    source = notebook.text
    assert 'from bulletjournal.runtime import artifacts' in source
    assert 'import marimo as mo' in source
    assert 'import pandas as pd' in source


def test_notebook_assets_are_listed_through_dedicated_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='notes', title='Notes')
    assets.push(assets.DataFrame(pd.DataFrame({'value': [1, 2]})), name='table', title='Table', asset_type=assets.DataFrame)
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    pending = client.get('/api/v1/nodes/asset_node/assets')
    assert pending.status_code == 200
    assert [asset['asset_name'] for asset in pending.json()] == ['notes', 'table']
    assert all(asset['state'] == 'pending' for asset in pending.json())

    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    listed = client.get('/api/v1/nodes/asset_node/assets')
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert [asset['asset_name'] for asset in listed_payload] == ['notes', 'table']
    assert listed_payload[0]['asset_type'] == 'markdown'
    assert listed_payload[1]['asset_type'] == 'dataframe'

    table_asset = client.get('/api/v1/nodes/asset_node/assets/table')
    assert table_asset.status_code == 200
    assert table_asset.json()['objects'][0]['object_role'] == 'backing_dataset'


def test_dataframe_asset_prepare_returns_paginated_sorted_table(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import assets

@app.cell
def _():
    frame = pd.DataFrame({
        'value': list(range(12, 0, -1)),
        'label': [f'row_{value}' for value in range(12, 0, -1)],
        'created': pd.date_range('2026-01-01', periods=12, freq='D'),
    })
    assets.push(assets.DataFrame(frame), name='table', title='Table', asset_type=assets.DataFrame)
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    prepared = client.post(
        '/api/v1/assets/asset_node/table/prepare',
        json={
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'value', 'direction': 'asc'}],
                'filters': [{'kind': 'range', 'column': 'value', 'lower': 3, 'upper': 11}],
            }
        },
    )

    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload['state'] == 'ready'
    assert payload['resolved_modifiers']['page'] == {'index': 0, 'size': 10}
    assert payload['resolved_modifiers']['sort'] == [{'column': 'value', 'direction': 'asc'}]
    assert payload['resolved_modifiers']['filters'] == [
        {'kind': 'range', 'column': 'value', 'value_type': 'numeric', 'lower': 3, 'upper': 11}
    ]
    table_payload = payload['payloads']['table']
    assert table_payload['kind'] == 'table'
    assert table_payload['rows_total'] == 9
    assert table_payload['page'] == {'index': 0, 'size': 10}
    assert table_payload['sort'] == [{'column': 'value', 'direction': 'asc'}]
    assert [column['id'] for column in table_payload['columns']] == ['value', 'label', 'created']
    assert table_payload['columns'][0]['filter_kinds'] == ['range', 'value']
    assert table_payload['columns'][1]['filter_kinds'] == ['value', 'regex']
    assert table_payload['columns'][2]['filter_kinds'] == ['range', 'value']
    assert [row['value'] for row in table_payload['rows']] == [3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert [row['created'] for row in table_payload['rows']] == [
        '2026-01-10T00:00:00',
        '2026-01-09T00:00:00',
        '2026-01-08T00:00:00',
        '2026-01-07T00:00:00',
        '2026-01-06T00:00:00',
        '2026-01-05T00:00:00',
        '2026-01-04T00:00:00',
        '2026-01-03T00:00:00',
        '2026-01-02T00:00:00',
    ]

    prepared_value_filter = client.post(
        '/api/v1/assets/asset_node/table/prepare',
        json={
            'modifier_overrides': {
                'filters': [{'kind': 'value', 'column': 'label', 'values': ['row_2', 'row_4']}],
                'sort': [{'column': 'value', 'direction': 'asc'}],
            }
        },
    )

    assert prepared_value_filter.status_code == 200
    value_payload = prepared_value_filter.json()
    assert [row['value'] for row in value_payload['payloads']['table']['rows']] == [2, 4]
    assert value_payload['resolved_modifiers']['filters'] == [
        {'kind': 'value', 'column': 'label', 'value_type': 'text', 'values': ['row_2', 'row_4'], 'include_null': False}
    ]

    prepared_regex_filter = client.post(
        '/api/v1/assets/asset_node/table/prepare',
        json={
            'modifier_overrides': {
                'filters': [{'kind': 'regex', 'column': 'label', 'pattern': '^row_1[0-2]$'}],
            }
        },
    )

    assert prepared_regex_filter.status_code == 200
    regex_payload = prepared_regex_filter.json()
    assert [row['label'] for row in regex_payload['payloads']['table']['rows']] == ['row_12', 'row_11', 'row_10']

    prepared_date_filter = client.post(
        '/api/v1/assets/asset_node/table/prepare',
        json={
            'modifier_overrides': {
                'filters': [
                    {
                        'kind': 'range',
                        'column': 'created',
                        'lower': '2026-01-03T00:00:00',
                        'upper': '2026-01-05T00:00:00',
                    }
                ],
            }
        },
    )

    assert prepared_date_filter.status_code == 200
    date_payload = prepared_date_filter.json()
    assert [row['value'] for row in date_payload['payloads']['table']['rows']] == [10, 9, 8]


def test_histogram_asset_prepare_returns_chart_and_linked_table(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import assets

@app.cell
def _():
    frame = pd.DataFrame({
        'value': [1, 2, 2, 3, 4, 5, 6, 7, 8, 9],
        'label': [f'row_{value}' for value in [1, 2, 2, 3, 4, 5, 6, 7, 8, 9]],
        'segment': ['a', 'a', 'b', 'b', 'a', 'b', 'a', 'b', 'a', 'b'],
        'weight': [1, 2, 2, 3, 4, 5, 6, 7, 8, 9],
        'palette': ['red', 'red', 'blue', 'blue', 'green', 'green', 'red', 'blue', 'green', 'red'],
    })
    assets.push(
        assets.Histogram(frame, x='value', bins=4, shape='segment', size='weight', color='palette'),
        name='value_hist',
        title='Value histogram',
        asset_type=assets.Histogram,
    )
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    prepared = client.post(
        '/api/v1/assets/asset_node/value_hist/prepare',
        json={
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'value', 'direction': 'asc'}],
                'filters': [{'kind': 'range', 'column': 'value', 'lower': 2, 'upper': 8}],
                'bin_count': 4,
            },
            'transient_modifiers': {
                'selection_range': {'lower': 5, 'upper': 6.5},
            },
        },
    )

    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload['resolved_modifiers']['bin_count'] == 4
    assert payload['resolved_modifiers']['filters'] == [
        {'kind': 'range', 'column': 'value', 'value_type': 'numeric', 'lower': 2, 'upper': 8}
    ]
    histogram_payload = payload['payloads']['main']
    assert histogram_payload['kind'] == 'histogram'
    assert histogram_payload['x_column'] == 'value'
    assert histogram_payload['rows_total'] == 8
    assert histogram_payload['non_null_rows'] == 8
    assert [entry['count'] for entry in histogram_payload['bins']] == [3, 1, 2, 2]
    assert histogram_payload['domain'] == {'min': 2.0, 'max': 8.0}
    table_payload = payload['payloads']['table']
    assert table_payload['kind'] == 'table'
    assert table_payload['rows_total'] == 2
    assert [row['value'] for row in table_payload['rows']] == [5, 6]


def test_scatter_plot_asset_prepare_returns_chart_and_linked_table(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import assets

@app.cell
def _():
    frame = pd.DataFrame({
        'x': [1, 2, 3, 4, 5, 6],
        'y': [10, 11, 12, 13, 14, 15],
        'group': ['circle', 'square', 'circle', 'triangle', 'square', 'triangle'],
        'weight': [100, 120, 140, 160, 180, 200],
        'palette': ['red', 'blue', 'red', 'green', 'blue', 'green'],
        'label': [f'row_{value}' for value in [1, 2, 3, 4, 5, 6]],
    })
    assets.push(
        assets.ScatterPlot(frame, x='x', y='y', shape='group', size='weight', color='palette'),
        name='xy_plot',
        title='XY plot',
        asset_type=assets.ScatterPlot,
    )
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    prepared = client.post(
        '/api/v1/assets/asset_node/xy_plot/prepare',
        json={
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'x', 'direction': 'asc'}],
                'filters': [{'kind': 'range', 'column': 'x', 'lower': 2, 'upper': 5}],
            },
            'transient_modifiers': {
                'selection_bounds': {
                    'x': {'lower': 3, 'upper': 4.1},
                    'y': {'lower': 12, 'upper': 13.1},
                },
            },
        },
    )

    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload['resolved_modifiers']['filters'] == [
        {'kind': 'range', 'column': 'x', 'value_type': 'numeric', 'lower': 2, 'upper': 5}
    ]
    scatter_payload = payload['payloads']['main']
    assert scatter_payload['kind'] == 'scatter_plot'
    assert scatter_payload['x_column'] == 'x'
    assert scatter_payload['y_column'] == 'y'
    assert scatter_payload['shape_column'] == 'group'
    assert scatter_payload['size_column'] == 'weight'
    assert scatter_payload['size_kind'] == 'quantitative'
    assert scatter_payload['color_column'] == 'palette'
    assert scatter_payload['color_kind'] == 'nominal'
    assert scatter_payload['rows_total'] == 4
    assert scatter_payload['non_null_rows'] == 4
    assert scatter_payload['plotted_rows'] == 4
    assert scatter_payload['sampled'] is False
    assert scatter_payload['domain'] == {
        'x': {'min': 2.0, 'max': 5.0},
        'y': {'min': 11.0, 'max': 14.0},
    }
    assert scatter_payload['points'] == [
        {'row_index': 1, 'x': 2, 'y': 11, 'shape': 'square', 'size': 120, 'color': 'blue'},
        {'row_index': 2, 'x': 3, 'y': 12, 'shape': 'circle', 'size': 140, 'color': 'red'},
        {'row_index': 3, 'x': 4, 'y': 13, 'shape': 'triangle', 'size': 160, 'color': 'green'},
        {'row_index': 4, 'x': 5, 'y': 14, 'shape': 'square', 'size': 180, 'color': 'blue'},
    ]
    table_payload = payload['payloads']['table']
    assert table_payload['kind'] == 'table'
    assert table_payload['rows_total'] == 2
    assert [row['label'] for row in table_payload['rows']] == ['row_3', 'row_4']

    selected_point = client.post(
        '/api/v1/assets/asset_node/xy_plot/prepare',
        json={
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'x', 'direction': 'asc'}],
                'filters': [{'kind': 'range', 'column': 'x', 'lower': 2, 'upper': 5}],
            },
            'transient_modifiers': {
                'selected_row_index': 3,
            },
        },
    )

    assert selected_point.status_code == 200
    selected_point_payload = selected_point.json()['payloads']['table']
    assert selected_point_payload['rows_total'] == 1
    assert [row['label'] for row in selected_point_payload['rows']] == ['row_4']


def test_pie_chart_asset_prepare_returns_chart_and_linked_table(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    import pandas as pd
    from bulletjournal.runtime import assets

@app.cell
def _():
    frame = pd.DataFrame({
        'segment': ['a', 'a', 'b', 'c', 'c', 'c', None],
        'value': [1, 2, 3, 4, 5, 6, 7],
        'label': ['row_1', 'row_2', 'row_3', 'row_4', 'row_5', 'row_6', 'row_7'],
    })
    assets.push(
        assets.PieChart(frame, category='segment', color={'a': '#f00', 'c': '#00f'}),
        name='segment_share',
        title='Segment share',
        asset_type=assets.PieChart,
    )
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    prepared = client.post(
        '/api/v1/assets/asset_node/segment_share/prepare',
        json={
            'modifier_overrides': {
                'page': {'index': 0, 'size': 10},
                'sort': [{'column': 'value', 'direction': 'asc'}],
                'filters': [{'kind': 'range', 'column': 'value', 'lower': 2, 'upper': 6}],
            },
            'transient_modifiers': {
                'selected_categories': ['c'],
            },
        },
    )

    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload['resolved_modifiers']['filters'] == [
        {'kind': 'range', 'column': 'value', 'value_type': 'numeric', 'lower': 2, 'upper': 6}
    ]
    pie_payload = payload['payloads']['main']
    assert pie_payload['kind'] == 'pie_chart'
    assert pie_payload['category_column'] == 'segment'
    assert pie_payload['rows_total'] == 5
    assert pie_payload['non_null_rows'] == 5
    assert pie_payload['slices'] == [
        {'value': 'c', 'label': 'c', 'count': 3, 'share': 0.6, 'color': '#00f'},
        {'value': 'a', 'label': 'a', 'count': 1, 'share': 0.2, 'color': '#f00'},
        {'value': 'b', 'label': 'b', 'count': 1, 'share': 0.2, 'color': '#94a3b8'},
    ]
    table_payload = payload['payloads']['table']
    assert table_payload['kind'] == 'table'
    assert table_payload['rows_total'] == 3
    assert [row['label'] for row in table_payload['rows']] == ['row_4', 'row_5', 'row_6']


def test_saved_dashboard_crud_and_conflict_flow(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'
    notebook_path.write_text(
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
        + '\n',
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)
    run = client.post('/api/v1/nodes/asset_node/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200

    saved = client.post(
        '/api/v1/nodes/asset_node/dashboards',
        json={
            'title': 'Evaluation Dashboard',
            'panels': [
                {
                    'node_id': 'asset_node',
                    'asset_name': 'notes',
                    'visible': True,
                    'position': 0,
                    'modifier_overrides': {},
                }
            ],
        },
    )
    assert saved.status_code == 200
    created = saved.json()
    dashboard_id = created['dashboard_id']
    assert created['dashboard_url'] == f'/dashboards/{dashboard_id}'
    assert (project_root / 'dashboards' / f'{dashboard_id}.json').is_file()

    snapshot = client.get('/api/v1/project/snapshot').json()
    dashboard_node = next(node for node in snapshot['graph']['nodes'] if node['id'] == dashboard_id)
    assert dashboard_node['kind'] == 'dashboard'
    assert dashboard_node['title'] == 'Evaluation Dashboard'

    loaded = client.get(f'/api/v1/dashboards/{dashboard_id}')
    assert loaded.status_code == 200
    assert loaded.json()['sources'] == [{'node_id': 'asset_node'}]
    assert loaded.json()['panels'][0]['panel_id'] == 'asset_node/notes'

    updated = client.patch(
        f'/api/v1/dashboards/{dashboard_id}',
        json={
            'dashboard_version': loaded.json()['version'],
            'title': 'Updated Dashboard',
            'panels': [
                {
                    'panel_id': 'asset_node/notes',
                    'node_id': 'asset_node',
                    'asset_name': 'notes',
                    'visible': False,
                    'position': 0,
                    'modifier_overrides': {},
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()['version'] == 2
    assert updated.json()['title'] == 'Updated Dashboard'
    assert updated.json()['panels'][0]['visible'] is False

    conflict = client.patch(
        f'/api/v1/dashboards/{dashboard_id}',
        json={
            'dashboard_version': 1,
            'title': 'Conflicting Title',
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()['dashboard']['version'] == 2

    deleted = client.delete(f'/api/v1/dashboards/{dashboard_id}')
    assert deleted.status_code == 200
    assert not (project_root / 'dashboards' / f'{dashboard_id}.json').exists()
    refreshed_snapshot = client.get('/api/v1/project/snapshot').json()
    assert all(node['id'] != dashboard_id for node in refreshed_snapshot['graph']['nodes'])


def test_saved_dashboards_refresh_when_notebook_asset_declarations_change(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    patched = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'asset_node',
                    'title': 'Asset Node',
                }
            ],
        },
    )
    assert patched.status_code == 200

    notebook_path = project_root / 'notebooks' / 'asset_node.py'

    def write_notebook(asset_lines: list[str]) -> None:
        notebook_path.write_text(
            (
                """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets
    import pandas as pd

@app.cell
def _():
""".strip()
                + '\n'
                + '\n'.join(f'    {line}' for line in asset_lines)
                + """
    return
"""
            ),
            encoding='utf-8',
        )

    write_notebook(
        [
            "assets.push(assets.Markdown('hello'), name='notes', title='Notes')",
        ]
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    saved = client.post('/api/v1/nodes/asset_node/dashboards', json={'title': 'Evaluation Dashboard'})
    assert saved.status_code == 200
    dashboard_id = saved.json()['dashboard_id']

    initial_dashboard = client.get(f'/api/v1/dashboards/{dashboard_id}')
    assert initial_dashboard.status_code == 200
    assert [panel['asset_name'] for panel in initial_dashboard.json()['panels']] == ['notes']

    write_notebook(
        [
            "assets.push(assets.Markdown('hello'), name='report', title='Report')",
            "assets.push(assets.DataFrame(pd.DataFrame({'value': [1, 2]})), name='table', title='Table', asset_type=assets.DataFrame)",
        ]
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    refreshed_dashboard = client.get(f'/api/v1/dashboards/{dashboard_id}')
    assert refreshed_dashboard.status_code == 200
    assert [panel['asset_name'] for panel in refreshed_dashboard.json()['panels']] == ['report', 'table']

    refreshed_snapshot = client.get('/api/v1/project/snapshot').json()
    dashboard_node = next(node for node in refreshed_snapshot['graph']['nodes'] if node['id'] == dashboard_id)
    assert dashboard_node['ui']['panel_count'] == 2

    write_notebook(
        [
            "assets.push(assets.DataFrame(pd.DataFrame({'value': [1, 2]})), name='table', title='Table', asset_type=assets.DataFrame)",
        ]
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    trimmed_dashboard = client.get(f'/api/v1/dashboards/{dashboard_id}')
    assert trimmed_dashboard.status_code == 200
    assert [panel['asset_name'] for panel in trimmed_dashboard.json()['panels']] == ['table']


def test_graph_patch_rejects_unknown_operation_fields(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    invalid = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                    'unexpected': 'nope',
                }
            ],
        },
    )

    assert invalid.status_code == 422


def test_cors_allows_local_origin_only(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    allowed = client.get('/healthz', headers={'Origin': 'http://localhost:8765'})
    blocked = client.get('/healthz', headers={'Origin': 'https://example.com'})

    assert allowed.headers.get('access-control-allow-origin') == 'http://localhost:8765'
    assert blocked.headers.get('access-control-allow-origin') is None


def test_graph_layout_patch_accepts_position_only_updates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                    'x': 100,
                    'y': 120,
                    'w': 480,
                    'h': 260,
                }
            ],
        },
    )
    assert created.status_code == 200

    moved = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_layout',
                    'node_id': 'sample_node',
                    'x': 220,
                    'y': 260,
                }
            ],
        },
    )

    assert moved.status_code == 200
    layout = next(item for item in moved.json()['layout'] if item['node_id'] == 'sample_node')
    assert layout['x'] == 220
    assert layout['y'] == 260
    assert layout['w'] == 480
    assert layout['h'] == 260


def test_warning_notice_can_be_dismissed_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        source.replace(
            "sample_count = artifacts.pull(name='sample_count', data_type=int, default=10)",
            "sample_count = artifacts.pull(name='sample_count', data_type='mystery', default=10)",
        ),
        encoding='utf-8',
    )
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    warning = next(issue for issue in snapshot['validation_issues'] if issue['severity'] == 'warning')
    assert any(issue['issue_id'] == warning['issue_id'] for issue in snapshot['notices'])

    dismissed = client.post(f'/api/v1/notices/{warning["issue_id"]}/dismiss')

    assert dismissed.status_code == 200
    refreshed = client.get('/api/v1/project/snapshot').json()
    assert all(issue['issue_id'] != warning['issue_id'] for issue in refreshed['notices'])


def test_error_notice_can_be_dismissed_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    notebook_path.write_text(notebook_path.read_text(encoding='utf-8') + '\nbroken =\n', encoding='utf-8')
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    error_issue = next(issue for issue in snapshot['validation_issues'] if issue['severity'] == 'error')

    dismissed = client.post(f'/api/v1/notices/{error_issue["issue_id"]}/dismiss')

    assert dismissed.status_code == 200
    refreshed = client.get('/api/v1/project/snapshot').json()
    assert all(issue['issue_id'] != error_issue['issue_id'] for issue in refreshed['notices'])


def test_run_session_can_be_stopped_via_api(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']

    stopped = client.post(f'/api/v1/sessions/{session_id}/stop')
    assert stopped.status_code == 200
    assert stopped.json()['status'] == 'stopped'
    assert container.run_service.session_manager.get(session_id) is None


def test_file_input_artifact_name_round_trips_in_snapshot(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'uploaded_file',
                    'title': 'Uploaded File',
                    'artifact_name': 'dataset',
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'uploaded_file')
    assert node['ui']['artifact_name'] == 'dataset'
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_invalid_notebook_changes_keep_previous_ports_and_surface_errors(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    original_source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        original_source.replace(
            "artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, description='Sample output frame')",
            "artifacts.push(frame, name='renamed_df', data_type=pd.DataFrame, description='Sample output frame')\n    broken =",
        ),
        encoding='utf-8',
    )

    container = app.state.container
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'sample_node')

    assert [port['name'] for port in node['interface']['outputs']] == ['sample_df']
    assert any(
        issue['code'] == 'invalid_syntax'
        for issue in snapshot['validation_issues']
        if issue['node_id'] == 'sample_node'
    )
    assert node['state'] == 'error'


def test_unparsable_marimo_cell_keeps_previous_ports_and_surfaces_errors(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    notebook_path = project_root / 'notebooks' / 'sample_node.py'
    original_source = notebook_path.read_text(encoding='utf-8')
    notebook_path.write_text(
        original_source.replace(
            "@app.cell\ndef _(pd, sample_count):\n    frame = pd.DataFrame({'value': list(range(sample_count))})\n    artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, description='Sample output frame')\n    return frame",
            'app._unparsable_cell(\n    r"""\nframe = pd.DataFrame({\'value\': list(range(sample_count))})\nartifacts.push(frame, name=\'renamed_df\', data_type=pd.DataFrame, description=\'Sample output frame\')\nbroken =\nreturn frame\n"""\n)',
        ),
        encoding='utf-8',
    )

    container = app.state.container
    container.project_service.reparse_notebook_by_path(notebook_path)

    snapshot = client.get('/api/v1/project/snapshot').json()
    node = next(item for item in snapshot['graph']['nodes'] if item['id'] == 'sample_node')

    assert [port['name'] for port in node['interface']['outputs']] == ['sample_df']
    assert any(
        issue['code'] == 'invalid_syntax'
        for issue in snapshot['validation_issues']
        if issue['node_id'] == 'sample_node'
    )
    assert node['state'] == 'error'


def test_graph_patch_accepts_inline_notebook_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    notebook_source = (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    value = 7
    return value

@app.cell
def _(value):
    artifacts.push(value, name='value', data_type=int)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'inline_source',
                    'title': 'Inline Source',
                    'source_text': notebook_source,
                }
            ],
        },
    )

    assert created.status_code == 200
    notebook_path = project_root / 'notebooks' / 'inline_source.py'
    assert notebook_path.read_text(encoding='utf-8') == notebook_source.replace(
        'app = marimo.App()',
        "app = marimo.App(app_title='inline_source')",
    )


def test_snapshot_includes_pipeline_templates(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')

    assert opened.status_code == 200
    templates = opened.json()['templates']
    pipeline = next(item for item in templates if item['kind'] == 'pipeline')
    assert pipeline['ref'] == 'examples/example_iris_pipeline'
    assert pipeline['definition']['nodes']


def test_graph_patch_can_add_and_update_organizer_node(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_organizer_node',
                    'node_id': 'organizer',
                    'title': 'Organizer',
                    'x': 240,
                    'y': 180,
                }
            ],
        },
    )

    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_organizer_ports',
                    'node_id': 'organizer',
                    'ports': [
                        {'key': 'dataset', 'name': 'dataset', 'data_type': 'file'},
                        {'key': 'count', 'name': 'sample_count', 'data_type': 'int'},
                    ],
                }
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    organizer = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'organizer')

    assert organizer['kind'] == 'organizer'
    assert organizer['ui']['organizer_ports'][1]['name'] == 'sample_count'
    assert [port['name'] for port in organizer['interface']['inputs']] == ['dataset', 'count']
    assert organizer['interface']['inputs'][1]['label'] == 'sample_count'


def test_graph_patch_can_add_edge_to_new_organizer_port_in_same_request(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'source',
                    'title': 'Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_organizer_node',
                    'node_id': 'organizer',
                    'title': 'Organizer',
                    'x': 240,
                    'y': 180,
                },
            ],
        },
    )
    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_organizer_ports',
                    'node_id': 'organizer',
                    'ports': [
                        {'key': 'value', 'name': 'iris_dataframe', 'data_type': 'int'},
                    ],
                },
                {
                    'type': 'add_edge',
                    'source_node': 'source',
                    'source_port': 'value',
                    'target_node': 'organizer',
                    'target_port': 'value',
                },
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    assert any(
        edge['source_node'] == 'source'
        and edge['source_port'] == 'value'
        and edge['target_node'] == 'organizer'
        and edge['target_port'] == 'value'
        for edge in snapshot['graph']['edges']
    )


def test_graph_patch_can_add_and_style_area_node(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_area_node',
                    'node_id': 'area',
                    'title': 'Ingestion',
                    'x': 120,
                    'y': 160,
                    'w': 480,
                    'h': 280,
                }
            ],
        },
    )

    assert created.status_code == 200

    updated = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_area_style',
                    'node_id': 'area',
                    'title_position': 'bottom-center',
                    'color': 'purple',
                    'filled': False,
                }
            ],
        },
    )

    assert updated.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    area = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'area')
    layout = next(entry for entry in snapshot['graph']['layout'] if entry['node_id'] == 'area')

    assert area['kind'] == 'area'
    assert area['title'] == 'Ingestion'
    assert area['ui']['title_position'] == 'bottom-center'
    assert area['ui']['area_color'] == 'purple'
    assert area['ui']['area_filled'] is False
    assert layout['w'] == 480
    assert layout['h'] == 280


def test_graph_patch_can_add_pipeline_template(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                    'x': 200,
                    'y': 240,
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert {'constant', 'example_1', 'example_2', 'example_3', 'example_4'} <= node_ids
    constant_node = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'constant')
    assert constant_node['interface']['outputs'][0]['name'] == 'file'
    edge_ids = {edge['id'] for edge in snapshot['graph']['edges']}
    assert 'constant.file__example_1.iris_csv' in edge_ids
    layout_by_node = {entry['node_id']: entry for entry in snapshot['graph']['layout']}
    assert layout_by_node['constant']['x'] == 200
    assert layout_by_node['example_4']['y'] == 240
    assert layout_by_node['constant']['y'] - layout_by_node['example_4']['y'] == 240


def test_graph_patch_requires_prefix_when_pipeline_template_collides(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )
    assert created.status_code == 200

    duplicate = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )

    assert duplicate.status_code == 409
    assert 'Use a prefix to instantiate it' in duplicate.json()['detail']


def test_graph_patch_accepts_prefixed_pipeline_template(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    first = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )
    assert first.status_code == 200

    second = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': first.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                    'node_id_prefix': 'study_b',
                }
            ],
        },
    )

    assert second.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert {
        'study_b_constant',
        'study_b_example_1',
        'study_b_example_2',
        'study_b_example_3',
        'study_b_example_4',
    } <= node_ids


def test_pipeline_constants_do_not_force_prefix_collisions(tmp_path, monkeypatch) -> None:
    pipeline_source = json.dumps(
        {
            'title': 'Constant Only',
            'nodes': [
                {
                    'id': 'source',
                    'title': 'Source',
                    'kind': 'constant',
                    'artifact_name': 'value',
                    'data_type': 'int',
                    'value': 7,
                }
            ],
            'edges': [],
            'layout': [{'node_id': 'source', 'x': 80, 'y': 120, 'w': 100, 'h': 40}],
        }
    )

    class Provider:
        provider_name = 'acme'
        provider_revision = '0.1.0'

        def list_notebook_templates(self):
            return []

        def list_pipeline_templates(self):
            return [
                {
                    'name': 'constant_only',
                    'ref': 'acme/constant_only',
                    'title': 'Constant Only',
                    'path': 'pipelines/constant_only.json',
                    'hidden': False,
                }
            ]

        def load_notebook_template(self, name: str) -> str:
            return ''

        def load_pipeline_template(self, name: str) -> str:
            return pipeline_source if name == 'constant_only' else ''

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [Provider()])

    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    first = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [{'type': 'add_pipeline_template', 'template_ref': 'acme/constant_only'}],
        },
    )
    assert first.status_code == 200

    second = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': first.json()['meta']['graph_version'],
            'operations': [{'type': 'add_pipeline_template', 'template_ref': 'acme/constant_only'}],
        },
    )
    assert second.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    node_ids = {node['id'] for node in snapshot['graph']['nodes']}
    assert {'constant', 'constant_2'} <= node_ids


def test_pipeline_template_constant_value_is_ready_immediately(tmp_path, monkeypatch) -> None:
    pipeline_source = json.dumps(
        {
            'title': 'Prepopulated Constant',
            'nodes': [
                {
                    'id': 'threshold_source',
                    'title': 'Threshold Source',
                    'kind': 'constant',
                    'artifact_name': 'sample_count',
                    'data_type': 'int',
                    'value': 7,
                },
                {
                    'id': 'consumer',
                    'title': 'Consumer',
                    'kind': 'notebook',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
            'edges': [
                {
                    'source_node': 'threshold_source',
                    'source_port': 'sample_count',
                    'target_node': 'consumer',
                    'target_port': 'sample_count',
                }
            ],
            'layout': [
                {'node_id': 'threshold_source', 'x': 80, 'y': 120, 'w': 100, 'h': 40},
                {'node_id': 'consumer', 'x': 260, 'y': 120, 'w': 320, 'h': 200},
            ],
        }
    )

    class Provider:
        provider_name = 'acme'
        provider_revision = '0.1.0'

        def list_notebook_templates(self):
            return []

        def list_pipeline_templates(self):
            return [
                {
                    'name': 'prepopulated_constant',
                    'ref': 'acme/prepopulated_constant',
                    'title': 'Prepopulated Constant',
                    'path': 'pipelines/prepopulated_constant.json',
                    'hidden': False,
                }
            ]

        def load_notebook_template(self, name: str) -> str:
            return ''

        def load_pipeline_template(self, name: str) -> str:
            return pipeline_source if name == 'prepopulated_constant' else ''

    monkeypatch.setattr('bulletjournal.services.template_service.discover_template_providers', lambda: [Provider()])

    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [{'type': 'add_pipeline_template', 'template_ref': 'acme/prepopulated_constant'}],
        },
    )
    assert created.status_code == 200

    artifact = client.get('/api/v1/artifacts/constant/sample_count')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'
    assert artifact.json()['preview']['repr'] == '7'

    run = client.post('/api/v1/nodes/consumer/run', json={'mode': 'run_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    output = client.get('/api/v1/artifacts/consumer/sample_df')
    assert output.status_code == 200
    assert output.json()['preview']['rows'] == 7


def test_uploaded_constant_file_unblocks_downstream_notebook_run(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_pipeline_template',
                    'template_ref': 'builtin/example_iris_pipeline',
                }
            ],
        },
    )
    assert created.status_code == 200

    blocked = client.post('/api/v1/nodes/example_1/run', json={'mode': 'run_stale', 'action': 'run_upstream'})
    assert blocked.status_code == 200
    assert blocked.json()['status'] == 'blocked'
    assert blocked.json()['blocked_inputs'][0]['source'] == 'constant/file'

    csv_bytes = b'sepal_length,sepal_width,petal_length,petal_width,species\n5.1,3.5,1.4,0.2,setosa\n'
    upload = client.post(
        '/api/v1/constants/constant/upload',
        content=csv_bytes,
        headers={'X-Filename': 'iris.csv', 'Content-Type': 'text/csv'},
    )
    assert upload.status_code == 200
    assert upload.json()['artifact_name'] == 'file'
    assert upload.json()['state'] == 'ready'

    run = client.post('/api/v1/nodes/example_1/run', json={'mode': 'run_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    dataframe = client.get('/api/v1/artifacts/example_1/iris_dataframe')
    assert dataframe.status_code == 200
    assert dataframe.json()['preview']['rows'] == 1


def test_uploaded_dataframe_constant_supports_semicolon_separator(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'frame_source',
                    'title': 'Frame Source',
                    'data_type': 'pandas.DataFrame',
                }
            ],
        },
    )
    assert created.status_code == 200

    upload = client.post(
        '/api/v1/constants/frame_source/upload?csv_separator=semicolon',
        content=b'name;value\nalpha;1\nbeta;2\n',
        headers={'X-Filename': 'frame.csv', 'Content-Type': 'text/csv'},
    )
    assert upload.status_code == 200

    artifact = client.get('/api/v1/artifacts/frame_source/value')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    assert preview['kind'] == 'dataframe'
    assert preview['rows'] == 2
    assert preview['column_names'] == ['name', 'value']


def test_file_input_node_can_use_custom_artifact_name(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                    'artifact_name': 'dataset',
                }
            ],
        },
    )

    assert created.status_code == 200
    snapshot = client.get('/api/v1/project/snapshot')
    node = next(item for item in snapshot.json()['graph']['nodes'] if item['id'] == 'source_file')
    assert node['interface']['outputs'][0]['name'] == 'dataset'


def test_constant_node_can_populate_downstream_notebook(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'data_type': 'int',
                    'value': 42,
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    artifact = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert artifact.status_code == 200
    assert artifact.json()['preview']['rows'] == 42


def test_clearing_constant_value_returns_block_to_pending(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'data_type': 'int',
                    'value': 42,
                }
            ],
        },
    )
    assert created.status_code == 200

    cleared = client.post('/api/v1/constants/value_source/value', json={'clear': True})
    assert cleared.status_code == 200

    artifact = client.get('/api/v1/artifacts/value_source/value')
    assert artifact.status_code == 200
    payload = artifact.json()
    assert payload['state'] == 'pending'
    assert payload['current_version_id'] is None
    assert payload['artifact_hash'] is None


def test_constant_file_artifact_content_endpoint_renders_inline_image(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'image_source',
                    'title': 'Image Source',
                    'data_type': 'file',
                }
            ],
        },
    )
    assert created.status_code == 200

    png_bytes = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
        b'\x1f\x15\xc4\x89'
        b'\x00\x00\x00\x0cIDATx\x9cc``\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00'
        b'\x18\xdd\x8d\xb1'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    upload = client.post(
        '/api/v1/constants/image_source/upload',
        content=png_bytes,
        headers={
            'X-Filename': 'chart upload.png',
            'Content-Type': 'image/png',
        },
    )
    assert upload.status_code == 200

    artifact = client.get('/api/v1/artifacts/image_source/value')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    assert preview['kind'] == 'file'
    assert preview['image_inline'] is True
    assert preview['mime_type'] == 'image/png'

    content = client.get('/api/v1/artifacts/image_source/value/content')

    assert content.status_code == 200
    assert content.headers['content-type'].startswith('image/png')
    assert 'attachment' not in content.headers.get('content-disposition', '')
    assert content.content == png_bytes


def test_artifact_download_uses_artifact_name_and_extension(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download')

    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'filename="sample_df.parquet"' in response.headers['content-disposition']
    assert response.headers['content-type']


def test_dataframe_csv_download_returns_attachment(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download?format=csv')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert 'filename="sample_df.csv"' in response.headers['content-disposition']
    assert b'value\n' in response.content


def test_dataframe_csv_download_rejects_large_artifacts(tmp_path, monkeypatch) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    from bulletjournal.services import artifact_service as artifact_service_module

    monkeypatch.setattr(artifact_service_module, 'DATAFRAME_CSV_DOWNLOAD_MAX_BYTES', 1)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/sample_node/sample_df/download?format=csv')

    assert response.status_code == 400
    assert '100 MB' in response.text


def test_dataframe_xlsx_download_preserves_unicode(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'emoji_frame',
                    'title': 'Emoji Frame',
                    'source_text': (
                        'import marimo\n\n'
                        "app = marimo.App(app_title='Emoji Frame')\n\n"
                        'with app.setup:\n'
                        '    import pandas as pd\n'
                        '    from bulletjournal.runtime import artifacts\n\n'
                        '@app.cell\n'
                        'def _():\n'
                        "    frame = pd.DataFrame({'emoji': ['😀'], 'label': ['ok']})\n"
                        "    artifacts.push(frame, name='emoji_frame', data_type=pd.DataFrame, description='Emoji frame')\n"
                        '    return frame\n\n'
                        "if __name__ == '__main__':\n"
                        '    from bulletjournal.runtime.standalone import run_notebook_app\n\n'
                        '    run_notebook_app(app, __file__)\n'
                    ),
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post('/api/v1/nodes/emoji_frame/run', json={'mode': 'run_stale', 'action': 'use_stale'})
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    response = client.get('/api/v1/artifacts/emoji_frame/emoji_frame/download?format=xlsx')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    assert 'filename="emoji_frame.xlsx"' in response.headers['content-disposition']
    frame = pd.read_excel(io.BytesIO(response.content))
    assert frame.iloc[0]['emoji'] == '😀'


def test_dict_constant_preview_preserves_key_order(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'ordered_dict',
                    'title': 'Ordered Dict',
                    'data_type': 'dict',
                    'value': {'beta': 1, 'alpha': 2},
                }
            ],
        },
    )
    assert created.status_code == 200

    artifact = client.get('/api/v1/artifacts/ordered_dict/value')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    editor_text = preview['editor_text']
    assert editor_text.index('"beta"') < editor_text.index('"alpha"')
    assert preview['compact_repr'] == '{"beta":1,"alpha":2}'


def test_dict_constant_compact_preview_preserves_spaces_inside_strings(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_constant_node',
                    'node_id': 'stringy_dict',
                    'title': 'Stringy Dict',
                    'data_type': 'dict',
                    'value': {'key 1': 'a b c'},
                }
            ],
        },
    )
    assert created.status_code == 200

    artifact = client.get('/api/v1/artifacts/stringy_dict/value')
    assert artifact.status_code == 200
    assert artifact.json()['preview']['compact_repr'] == '{"key 1":"a b c"}'


def test_file_artifact_content_endpoint_renders_inline_image(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    project_id = opened.json()['project']['project_id']
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'image_source',
                    'title': 'Image Source',
                    'artifact_name': 'preview_image',
                }
            ],
        },
    )
    assert created.status_code == 200

    png_bytes = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
        b'\x1f\x15\xc4\x89'
        b'\x00\x00\x00\x0cIDATx\x9cc``\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00'
        b'\x18\xdd\x8d\xb1'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    upload = client.post(
        '/api/v1/file-inputs/image_source/upload',
        content=png_bytes,
        headers={
            'X-Filename': 'chart upload.png',
            'Content-Type': 'image/png',
        },
    )
    assert upload.status_code == 200

    artifact = client.get('/api/v1/artifacts/image_source/preview_image')
    assert artifact.status_code == 200
    preview = artifact.json()['preview']
    assert preview['kind'] == 'file'
    assert preview['image_inline'] is True
    assert preview['mime_type'] == 'image/png'

    content = client.get('/api/v1/artifacts/image_source/preview_image/content')

    assert content.status_code == 200
    assert content.headers['content-type'].startswith('image/png')
    assert 'attachment' not in content.headers.get('content-disposition', '')
    assert content.content == png_bytes


def test_notebook_download_endpoint_returns_python_source(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    response = client.get('/api/v1/nodes/sample_node/notebook/download')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/x-python')
    assert 'filename="sample_node.py"' in response.headers['content-disposition']
    assert 'import marimo' in response.text


def test_artifact_state_endpoints_can_mark_outputs_stale_and_ready(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    run = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    stale = client.post(
        '/api/v1/artifacts/sample_node/sample_df/state',
        json={'state': 'stale'},
    )
    assert stale.status_code == 200
    assert stale.json()['state'] == 'stale'

    ready = client.post(
        '/api/v1/artifacts/sample_node/sample_df/state',
        json={'state': 'ready'},
    )
    assert ready.status_code == 200
    assert ready.json()['state'] == 'ready'

    bulk_stale = client.post(
        '/api/v1/nodes/sample_node/outputs/state',
        json={'state': 'stale'},
    )
    assert bulk_stale.status_code == 200
    assert 'sample_df' in bulk_stale.json()['artifact_names']

    bulk_ready = client.post(
        '/api/v1/nodes/sample_node/outputs/state',
        json={'state': 'ready', 'only_current_state': 'stale'},
    )
    assert bulk_ready.status_code == 200
    assert 'sample_df' in bulk_ready.json()['artifact_names']

    refreshed = client.get('/api/v1/artifacts/sample_node/sample_df')
    assert refreshed.status_code == 200
    assert refreshed.json()['state'] == 'ready'


def test_marking_node_outputs_stale_also_stales_downstream_nodes(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'succeeded'

    bulk_stale = client.post(
        '/api/v1/nodes/value_source/outputs/state',
        json={'state': 'stale'},
    )
    assert bulk_stale.status_code == 200

    upstream = client.get('/api/v1/artifacts/value_source/value')
    downstream = client.get('/api/v1/artifacts/table_sink/sample_df')
    assert upstream.status_code == 200
    assert downstream.status_code == 200
    assert upstream.json()['state'] == 'stale'
    assert downstream.json()['state'] == 'stale'


def test_marking_outputs_ready_is_blocked_when_inputs_are_stale(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    run = client.post(
        '/api/v1/nodes/table_sink/run',
        json={'mode': 'run_stale', 'action': 'run_upstream'},
    )
    assert run.status_code == 200

    stale_source = client.post(
        '/api/v1/nodes/value_source/outputs/state',
        json={'state': 'stale'},
    )
    assert stale_source.status_code == 200

    blocked = client.post(
        '/api/v1/nodes/table_sink/outputs/state',
        json={'state': 'ready', 'only_current_state': 'stale'},
    )
    assert blocked.status_code == 400
    assert 'stale or pending inputs' in blocked.json()['detail']


def test_frozen_block_blocks_upstream_graph_edits_and_editor_sessions(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['table_sink']['ui']['frozen'] is True
    assert nodes['value_source']['ui']['frozen'] is True

    blocked_edit = client.post(
        '/api/v1/nodes/value_source/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert blocked_edit.status_code == 400
    assert 'frozen block' in blocked_edit.json()['detail']
    assert 'table_sink' in blocked_edit.json()['detail']

    blocked_graph_edit = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': frozen.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'remove_edge',
                    'edge_id': 'value_source.value__table_sink.sample_count',
                }
            ],
        },
    )
    assert blocked_graph_edit.status_code == 409
    assert 'frozen block' in blocked_graph_edit.json()['detail']
    assert 'table_sink' in blocked_graph_edit.json()['detail']


def test_freezing_notebook_is_blocked_when_upstream_editor_is_open(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    started = client.post(
        '/api/v1/nodes/value_source/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 409
    assert 'upstream editor' in frozen.json()['detail']
    assert 'value_source' in frozen.json()['detail']


def test_unfreezing_upstream_notebook_also_unfreezes_frozen_descendants(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'value_source',
                    'title': 'Value Source',
                    'template_ref': 'builtin/value_input',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'template_ref': 'builtin/test_starter_notebook',
                },
            ],
        },
    )
    assert created.status_code == 200

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'value_source',
                    'source_port': 'value',
                    'target_node': 'table_sink',
                    'target_port': 'sample_count',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    unfrozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': frozen.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'value_source',
                    'frozen': False,
                }
            ],
        },
    )
    assert unfrozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['value_source']['ui']['frozen'] is False
    assert nodes['table_sink']['ui']['frozen'] is False


def test_freezing_downstream_block_also_freezes_upstream_file_blocks(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                },
                {
                    'type': 'add_notebook_node',
                    'node_id': 'table_sink',
                    'title': 'Table Sink',
                    'source_text': (
                        'import marimo\n\n'
                        'app = marimo.App()\n\n'
                        'with app.setup:\n'
                        '    from bulletjournal.runtime import artifacts\n\n'
                        '@app.cell\n'
                        'def _():\n'
                        "    file_path = artifacts.pull_file(name='incoming')\n"
                        '    return file_path\n\n'
                        '@app.cell\n'
                        'def _(file_path):\n'
                        "    artifacts.push(len(file_path), name='path_length', data_type=int)\n"
                        '    return\n\n'
                        "if __name__ == '__main__':\n"
                        '    from bulletjournal.runtime.standalone import run_notebook_app\n\n'
                        '    run_notebook_app(app, __file__)\n'
                    ),
                },
            ],
        },
    )
    assert created.status_code == 200
    notebook_source = (project_root / 'notebooks' / 'table_sink.py').read_text(encoding='utf-8')
    assert "app = marimo.App(app_title='table_sink')" in notebook_source

    container.project_service.reparse_notebook_by_path(project_root / 'notebooks' / 'table_sink.py')

    connected = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_edge',
                    'source_node': 'source_file',
                    'source_port': 'file',
                    'target_node': 'table_sink',
                    'target_port': 'incoming',
                }
            ],
        },
    )
    assert connected.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': connected.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'table_sink',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['source_file']['ui']['frozen'] is True
    assert nodes['table_sink']['ui']['frozen'] is True


def test_frozen_file_input_blocks_upload_and_shows_frozen_state(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_file_input_node',
                    'node_id': 'source_file',
                    'title': 'Source File',
                }
            ],
        },
    )
    assert created.status_code == 200

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'source_file',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200

    snapshot = client.get('/api/v1/project/snapshot').json()
    nodes = {node['id']: node for node in snapshot['graph']['nodes']}
    assert nodes['source_file']['ui']['frozen'] is True

    blocked = client.post(
        '/api/v1/file-inputs/source_file/upload',
        content=b'hello world',
        headers={
            'content-type': 'text/plain',
            'x-filename': 'hello.txt',
        },
    )
    assert blocked.status_code == 400
    assert 'frozen block' in blocked.json()['detail']
    assert 'frozen' in blocked.json()['detail']


def test_deleting_node_stops_active_editor_session(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']
    assert container.run_service.session_manager.get(session_id) is not None

    deleted = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'delete_node',
                    'node_id': 'sample_node',
                }
            ],
        },
    )
    assert deleted.status_code == 200
    assert container.run_service.session_manager.get(session_id) is None


def test_freezing_node_stops_active_editor_session(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    container = app.state.container

    opened = client.get('/api/v1/project/snapshot')
    graph_version = opened.json()['graph']['meta']['graph_version']

    created = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': graph_version,
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'sample_node',
                    'title': 'Sample Node',
                }
            ],
        },
    )
    assert created.status_code == 200

    started = client.post(
        '/api/v1/nodes/sample_node/run',
        json={'mode': 'edit_run', 'action': None},
    )
    assert started.status_code == 200
    session_id = started.json()['session_id']
    assert container.run_service.session_manager.get(session_id) is not None

    frozen = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': created.json()['meta']['graph_version'],
            'operations': [
                {
                    'type': 'update_node_frozen',
                    'node_id': 'sample_node',
                    'frozen': True,
                }
            ],
        },
    )
    assert frozen.status_code == 200
    assert container.run_service.session_manager.get(session_id) is None
