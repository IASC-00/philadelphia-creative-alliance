import pytest

from tools.profile_schema import (
    DISCIPLINES,
    DISCIPLINE_SLUGS,
    discipline_by_slug,
)


def test_taxonomy_has_fourteen_unique_slugs():
    assert len(DISCIPLINES) == 14
    assert len(DISCIPLINE_SLUGS) == 14


def test_taxonomy_covers_digital_and_game_work():
    assert "digital-new-media" in DISCIPLINE_SLUGS
    assert "game-interactive" in DISCIPLINE_SLUGS


def test_hue_cycle_matches_the_mosaic_palette():
    from tools.mosaic import TILE_HUES

    for d in DISCIPLINES:
        assert d.hue in TILE_HUES
        assert 1 <= d.t <= 6


def test_lookup_by_slug():
    assert discipline_by_slug("painters").label == "Painters"


def test_lookup_rejects_unknown_slug():
    with pytest.raises(KeyError):
        discipline_by_slug("interpretive-mime")
