import pytest

from bulletjournal.runtime import assets


@pytest.mark.parametrize('display_mode', ['all', 'single', '2_columns', '3_columns'])
def test_collection_accepts_supported_display_modes(display_mode: str) -> None:
    assert assets.Collection(display_mode=display_mode).display_mode == display_mode


def test_collection_rejects_unknown_display_mode() -> None:
    with pytest.raises(ValueError, match='2_columns'):
        assets.Collection(display_mode='columns')
