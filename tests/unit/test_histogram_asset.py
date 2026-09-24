from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

from bulletjournal.assets.types.bar_chart import BarChart, bar_chart_modifier_defaults, serialize_bar_chart
from bulletjournal.assets.types.histogram import (
    Histogram,
    apply_histogram_group_selection,
    apply_histogram_selections,
    histogram_chart_modifier_defaults,
    prepare_histogram_main_payload,
    prepare_temporal_histogram_main_payload,
    resolve_histogram_selected_groups,
    serialize_histogram,
)
from bulletjournal.services.asset_prepare_service import AssetPrepareService


def test_histogram_validates_temporal_column_type() -> None:
    frame = pd.DataFrame({'value': ['a', 'b', 'c']})

    try:
        Histogram(frame, x='value')
    except TypeError as exc:
        assert 'numeric, date, or datetime dtype' in str(exc)
    else:
        raise AssertionError('Expected Histogram to reject non-numeric and non-temporal columns.')


def test_temporal_histogram_auto_granularity_uses_coarsest_supported_bucket_with_ten_bins() -> None:
    frame = pd.DataFrame(
        {
            'created_at': pd.date_range('2024-01-01', periods=12, freq='MS')
            + pd.Timedelta(days=14, hours=9, minutes=30),
        }
    )
    payload = prepare_temporal_histogram_main_payload(
        pl.DataFrame(frame.reset_index(drop=True)).lazy(),
        column='created_at',
        column_id_map={'created_at': 'created_at'},
        time_granularity='auto',
        histogram_category='datetime',
    )

    assert payload['time_granularity'] == 'month'
    assert payload['bin_count'] == 12
    assert payload['bins'][0]['label'] == 'Jan 1, 2024 to Jan 31, 2024'
    assert payload['bins'][-1]['label'] == 'Dec 1, 2024 to Dec 31, 2024'


def test_temporal_histogram_supports_hour_bins() -> None:
    frame = pd.DataFrame(
        {
            'created_at': [
                datetime(2024, 2, 1, 16, 5),
                datetime(2024, 2, 1, 16, 40),
                datetime(2024, 2, 1, 17, 10),
            ]
        }
    )
    payload = prepare_temporal_histogram_main_payload(
        pl.DataFrame(frame).lazy(),
        column='created_at',
        column_id_map={'created_at': 'created_at'},
        time_granularity='hour',
        histogram_category='datetime',
    )

    assert payload['time_granularity'] == 'hour'
    assert [entry['count'] for entry in payload['bins']] == [2, 1]
    assert payload['bins'][0]['label'] == '16:00 to 17:00'


def test_grouped_numeric_histogram_includes_zero_count_group_bins_and_orders_groups() -> None:
    payload = prepare_histogram_main_payload(
        pl.DataFrame({'value': [1, 2, 3], 'segment': ['b', 'a', 'b']}).lazy(),
        column='value',
        column_id_map={'value': 'value', 'segment': 'segment'},
        bin_count=2,
        group_column='segment',
        group_order=['a', 'b'],
        color_mapping_entries=[{'value': 'a', 'color': 'red'}, {'value': 'b', 'color': 'blue'}],
        default_color='#6b7280',
    )

    assert payload['group_column'] == 'segment'
    assert [(entry['index'], entry['group'], entry['count']) for entry in payload['bins']] == [
        (0, 'a', 0),
        (0, 'b', 1),
        (1, 'a', 1),
        (1, 'b', 1),
    ]
    assert [entry['color'] for entry in payload['bins']] == ['red', 'blue', 'red', 'blue']


def test_grouped_temporal_histogram_preserves_temporal_bin_metadata() -> None:
    payload = prepare_temporal_histogram_main_payload(
        pl.DataFrame(
            {
                'created_at': [datetime(2024, 2, 1, 16, 5), datetime(2024, 2, 1, 17, 10)],
                'segment': ['a', 'b'],
            }
        ).lazy(),
        column='created_at',
        column_id_map={'created_at': 'created_at', 'segment': 'segment'},
        time_granularity='hour',
        histogram_category='datetime',
        group_column='segment',
        group_order='category_desc',
        color_mapping_entries=None,
        default_color='#6b7280',
    )

    assert payload['group_column'] == 'segment'
    assert [(entry['index'], entry['group'], entry['count']) for entry in payload['bins']] == [
        (0, 'b', 0),
        (0, 'a', 1),
        (1, 'b', 1),
        (1, 'a', 0),
    ]
    assert payload['bins'][0]['label'] == '16:00 to 17:00'


def test_grouped_histogram_table_selection_combines_groups_and_ranges() -> None:
    frame = pl.DataFrame({'value': [1, 2, 3, 4], 'segment': ['a', 'b', 'a', 'b']}).lazy()
    selected_groups = resolve_histogram_selected_groups(
        {'selected_groups': ['a', 'a']}, column='segment', dtype=pl.String
    )

    selected = apply_histogram_group_selection(
        apply_histogram_selections(
            frame,
            'value',
            [{'lower': 2, 'upper': 4}],
            {'value': 'value', 'segment': 'segment'},
        ),
        'segment',
        selected_groups,
        {'value': 'value', 'segment': 'segment'},
    ).collect()

    assert selected.to_dicts() == [{'value': 3, 'segment': 'a'}]


def test_histogram_color_requires_group() -> None:
    try:
        Histogram(pd.DataFrame({'value': [1]}), x='value', color={'a': 'red'})
    except TypeError as exc:
        assert '`group` when `color`' in str(exc)
    else:
        raise AssertionError('Expected Histogram color to require a group column.')


def test_ungrouped_histogram_uses_explicit_color_in_numeric_and_temporal_payloads() -> None:
    numeric = Histogram(pd.DataFrame({'value': [1, 2]}), x='value', color='#00ff00')
    temporal = Histogram(pd.DataFrame({'value': [date(2020, 1, 1)]}), x='value', color='#ff0000')
    store = SimpleNamespace(persist_value=lambda *_args: {})
    for asset, expected in ((numeric, '#00ff00'), (temporal, '#ff0000')):
        definition = serialize_histogram(asset, object_store=store, title='Histogram', description=None).definition
        assert definition['histogram_default_color'] == expected
    numeric_payload = prepare_histogram_main_payload(
        pl.DataFrame({'value': [1, 2]}).lazy(),
        column='value',
        column_id_map={'value': 'value'},
        bin_count=2,
        default_color='#00ff00',
    )
    temporal_payload = prepare_temporal_histogram_main_payload(
        pl.DataFrame({'value': [date(2020, 1, 1)]}).lazy(),
        column='value',
        column_id_map={'value': 'value'},
        time_granularity='day',
        histogram_category='date',
        default_color='#ff0000',
    )
    assert {entry['color'] for entry in numeric_payload['bins']} == {'#00ff00'}
    assert {entry['color'] for entry in temporal_payload['bins']} == {'#ff0000'}


@pytest.mark.parametrize('normalization', ['none', 'max', 'sum', True, False])
def test_grouped_charts_accept_normalization_modes_and_legacy_booleans(normalization) -> None:
    frame = pd.DataFrame({'value': [1, 2], 'category': ['a', 'b'], 'group': ['x', 'y']})
    Histogram(frame, x='value', group='group', group_normalize=normalization)
    BarChart(frame, category='category', value='value', group='group', group_normalize=normalization)


@pytest.mark.parametrize('normalization', ['invalid', None, 1])
def test_grouped_charts_reject_unknown_normalization_modes(normalization) -> None:
    frame = pd.DataFrame({'value': [1, 2], 'category': ['a', 'b'], 'group': ['x', 'y']})
    with pytest.raises(TypeError, match='group_normalize'):
        Histogram(frame, x='value', group='group', group_normalize=normalization)
    with pytest.raises(TypeError, match='group_normalize'):
        BarChart(frame, category='category', value='value', group='group', group_normalize=normalization)


def test_grouped_chart_defaults_use_full_bar_width_and_intergroup_spacing() -> None:
    histogram_defaults = histogram_chart_modifier_defaults(
        title='Histogram',
        x_column='value',
        y_axis_label='Rows',
        bin_count=2,
        time_granularity=None,
        group_mode='grouped',
    )
    bar_defaults = bar_chart_modifier_defaults(
        title='Bar chart',
        category_column='category',
        y_axis_label='Value',
        group_mode='grouped',
    )
    for defaults in (histogram_defaults, bar_defaults):
        assert defaults['bar_width'] == 100
        assert defaults['group_spacing'] == 20
        assert defaults['group_normalize'] == 'none'


@pytest.mark.parametrize('mode, expected_width', [('grouped', 100), ('stacked', 90)])
def test_grouped_chart_serialization_resolves_bar_width_and_legacy_normalization(mode, expected_width) -> None:
    frame = pd.DataFrame({'value': [1, 2], 'category': ['a', 'b'], 'group': ['x', 'y']})
    store = SimpleNamespace(persist_value=lambda *_args: {})
    serialized = [
        serialize_histogram(
            Histogram(frame, x='value', group='group', group_mode=mode, group_normalize=True),
            object_store=store,
            title='Histogram',
            description=None,
        ),
        serialize_bar_chart(
            BarChart(frame, category='category', value='value', group='group', group_mode=mode, group_normalize=True),
            object_store=store,
            title='Bar chart',
            description=None,
        ),
    ]
    for asset in serialized:
        assert asset.default_modifiers['bar_width'] == expected_width
        assert asset.default_modifiers['group_normalize'] == 'sum'
        assert asset.default_modifiers['group_spacing'] == 20
        assert asset.default_modifiers['title']['size'] == 20


@pytest.mark.parametrize('value', ['none', 'max', 'sum', True, False])
def test_saved_group_normalization_overrides_accept_new_and_legacy_values(value) -> None:
    AssetPrepareService._validate_modifier_overrides(
        {'group_normalize': value},
        [{'id': 'group_normalize', 'kind': 'value', 'default_value': 'none'}],
    )
