import json
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

from bulletjournal.assets.types.bar_chart import BarChart, prepare_bar_chart_main_payload, serialize_bar_chart
from bulletjournal.assets.types.dataframe import DataFrame, prepare_dataframe, serialize_dataframe
from bulletjournal.assets.types.histogram import Histogram, serialize_histogram
from bulletjournal.assets.types.scatter_plot import ScatterPlot, serialize_scatter_plot


@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize(
    'categories',
    [
        ['zèbre', 'a"quoted"', 'line\nbreak', 'path\\name', '東京'],
        [10, 2, 30],
    ],
)
def test_bar_chart_category_order_matches_raw_values(grouped, categories) -> None:
    frame = pl.DataFrame({'category': categories, 'value': list(range(1, len(categories) + 1))})
    if grouped:
        frame = frame.with_columns(pl.lit('group').alias('group'))
    order = list(reversed(categories))
    payload = prepare_bar_chart_main_payload(
        frame.lazy(),
        category_column='category',
        value_column='value',
        aggregation='sum',
        column_id_map={name: name for name in frame.columns},
        color_mapping_entries=None,
        category_order=order,
        default_color=None,
        group_column='group' if grouped else None,
    )
    assert [bar['value'] for bar in payload['bars']] == order
    assert [bar['category_index'] for bar in payload['bars']] == list(range(len(categories)))


@pytest.mark.parametrize('category_order', ['value_asc', 'value_desc'])
def test_grouped_bar_chart_value_order_uses_category_totals(category_order) -> None:
    frame = pl.DataFrame({'category': ['a', 'a', 'b'], 'group': ['x', 'y', 'x'], 'value': [2, 8, 6]})
    payload = prepare_bar_chart_main_payload(
        frame.lazy(),
        category_column='category',
        value_column='value',
        aggregation='sum',
        column_id_map={name: name for name in frame.columns},
        color_mapping_entries=None,
        category_order=category_order,
        default_color=None,
        group_column='group',
    )
    expected = ['b', 'b', 'a', 'a'] if category_order == 'value_asc' else ['a', 'a', 'b', 'b']
    assert [bar['value'] for bar in payload['bars']] == expected


def test_table_preview_preserves_large_integers_in_json(tmp_path) -> None:
    value = 2103982719223071223
    path = tmp_path / 'table.parquet'
    pl.DataFrame(
        {
            'signed': pl.Series([value, -value, None], dtype=pl.Int64),
            'unsigned': pl.Series([2**64 - 1, 2**53 - 1, 2**53], dtype=pl.UInt64),
            'nested': [[value], [-value], []],
            'small': [1, 2, 3],
        }
    ).write_parquet(path)
    payloads, _ = prepare_dataframe(
        dataset_path=path,
        definition={},
        default_modifiers={},
        modifier_overrides={},
        transient_modifiers={},
    )
    table = json.loads(json.dumps(payloads['table']))
    assert table['rows'] == [
        {'signed': str(value), 'unsigned': str(2**64 - 1), 'nested': [str(value)], 'small': 1},
        {'signed': str(-value), 'unsigned': 2**53 - 1, 'nested': [str(-value)], 'small': 2},
        {'signed': None, 'unsigned': str(2**53), 'nested': [], 'small': 3},
    ]
    assert table['columns'][0]['data_type'] == 'Int64'


@pytest.mark.parametrize('lines', [1, 3, 10])
def test_dataframe_serializes_max_text_lines_without_changing_cell_contents(lines) -> None:
    frame = pd.DataFrame({'text': ['first\nsecond', 'long category name ' * 20]})
    store = SimpleNamespace(persist_value=lambda value, data_type: value)
    serialized = serialize_dataframe(
        DataFrame(frame, max_text_lines=lines), object_store=store, title='Table', description=None
    )
    assert serialized.definition['max_text_lines'] == lines
    assert serialized.default_modifiers['max_text_lines'] == lines
    modifier = next(entry for entry in serialized.modifier_schema if entry['id'] == 'max_text_lines')
    assert modifier['default_value'] == lines
    assert modifier['category'] == 'saved_view'
    assert (modifier['min_value'], modifier['max_value']) == (1, 10)
    assert serialized.objects[0].persisted.equals(frame)


@pytest.mark.parametrize('lines', [0, 11, -1, 1.5, '2', True, None])
def test_dataframe_rejects_invalid_max_text_lines(lines) -> None:
    with pytest.raises((TypeError, ValueError), match='max_text_lines'):
        DataFrame(pd.DataFrame(), max_text_lines=lines)


def test_dataframe_defaults_to_one_text_line() -> None:
    assert DataFrame(pd.DataFrame()).max_text_lines == 1


@pytest.mark.parametrize(
    ('definition', 'defaults', 'overrides', 'expected'),
    [
        ({}, {}, {}, 1),
        ({'max_text_lines': 3}, {}, {}, 3),
        ({}, {'max_text_lines': 4}, {}, 4),
        ({'max_text_lines': 3}, {'max_text_lines': 4}, {'max_text_lines': 10}, 10),
    ],
)
def test_dataframe_prepare_resolves_text_lines(definition, defaults, overrides, expected) -> None:
    _, resolved = prepare_dataframe(
        dataset_path=None,
        definition=definition,
        default_modifiers=defaults,
        modifier_overrides=overrides,
        transient_modifiers={},
    )
    assert resolved['max_text_lines'] == expected


@pytest.mark.parametrize(
    ('asset_class', 'serialize', 'kwargs'),
    [
        (BarChart, serialize_bar_chart, {'category': 'x', 'value': 'y'}),
        (Histogram, serialize_histogram, {'x': 'x'}),
        (ScatterPlot, serialize_scatter_plot, {'x': 'x', 'y': 'y'}),
    ],
)
def test_chart_tick_angles_default_and_custom_serialization(asset_class, serialize, kwargs) -> None:
    frame = pd.DataFrame({'x': [1, 2], 'y': [3, 4]})
    store = SimpleNamespace(persist_value=lambda value, data_type: value)
    for custom, expected in [
        ({}, (45, 0)),
        ({'x_axis': {'tick_angle': 0, 'tick_size': 18}, 'y_axis': {'tick_angle': 90}}, (0, 90)),
    ]:
        serialized = serialize(
            asset_class(frame, **kwargs, **custom), object_store=store, title='Chart', description=None
        )
        assert serialized.default_modifiers['x_axis']['tick_angle'] == expected[0]
        assert serialized.default_modifiers['y_axis']['tick_angle'] == expected[1]
        if custom:
            assert serialized.default_modifiers['x_axis']['tick_size'] == 18


@pytest.mark.parametrize('angle', [-45, 30, 180, '45', True])
def test_chart_rejects_unsupported_tick_angles(angle) -> None:
    with pytest.raises(TypeError, match='tick_angle'):
        ScatterPlot(pd.DataFrame({'x': [1], 'y': [2]}), x='x', y='y', x_axis={'tick_angle': angle})
