from datetime import datetime

import pandas as pd
import polars as pl

from bulletjournal.assets.types.time_histogram import TimeHistogram, prepare_time_histogram_main_payload


def test_time_histogram_validates_temporal_column() -> None:
    frame = pd.DataFrame({'value': [1, 2, 3]})

    try:
        TimeHistogram(frame, x='value')
    except TypeError as exc:
        assert 'date or datetime dtype' in str(exc)
    else:
        raise AssertionError('Expected TimeHistogram to reject non-temporal columns.')


def test_time_histogram_auto_granularity_uses_coarsest_supported_bucket_with_ten_bins() -> None:
    frame = pd.DataFrame(
        {
            'created_at': pd.date_range('2024-01-01', periods=12, freq='MS')
            + pd.Timedelta(days=14, hours=9, minutes=30),
        }
    )
    payload = prepare_time_histogram_main_payload(
        pl.DataFrame(frame.reset_index(drop=True)).lazy(),
        column='created_at',
        column_id_map={'created_at': 'created_at'},
        granularity='auto',
        histogram_category='datetime',
    )

    assert payload['time_granularity'] == 'month'
    assert payload['bin_count'] == 12
    assert payload['bins'][0]['label'] == 'Jan 1, 2024 to Jan 31, 2024'
    assert payload['bins'][-1]['label'] == 'Dec 1, 2024 to Dec 31, 2024'


def test_time_histogram_supports_hour_bins() -> None:
    frame = pd.DataFrame(
        {
            'created_at': [
                datetime(2024, 2, 1, 16, 5),
                datetime(2024, 2, 1, 16, 40),
                datetime(2024, 2, 1, 17, 10),
            ]
        }
    )
    payload = prepare_time_histogram_main_payload(
        pl.DataFrame(frame).lazy(),
        column='created_at',
        column_id_map={'created_at': 'created_at'},
        granularity='hour',
        histogram_category='datetime',
    )

    assert payload['time_granularity'] == 'hour'
    assert [entry['count'] for entry in payload['bins']] == [2, 1]
    assert payload['bins'][0]['label'] == '16:00 to 17:00'
