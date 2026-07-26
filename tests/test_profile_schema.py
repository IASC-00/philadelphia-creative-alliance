# ruff: noqa: S101
import json
from pathlib import Path

import pytest

from tools.profile_schema import (
    DISCIPLINES,
    DISCIPLINE_SLUGS,
    ProfileError,
    discipline_by_slug,
    load_all,
    load_profile,
    slugify,
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


VALID = {
    "slug": "example-artist",
    "name": "Example Artist",
    "disciplines": ["digital-new-media"],
    "tagline": "Makes things that move.",
    "bio": ["Paragraph one."],
    "location": "Philadelphia, PA",
    "portrait": {"src": "portrait.jpg", "alt": "A portrait."},
    "works": [{"src": "work-01.jpg", "alt": "A work.", "title": "Untitled"}],
    "links": [{"label": "Website", "url": "https://example.com"}],
    "availability": {
        "status": "open",
        "openTo": ["commissions"],
        "updated": "2026-07-26",
    },
    "consent": {"listed": True, "images": True, "date": "2026-07-26"},
    "joined": "2026-07-26",
    "live": True,
}


def write_profile(tmp_path: Path, **overrides) -> Path:
    data = {**VALID, **overrides}
    p = tmp_path / f"{data['slug']}.json"
    p.write_text(json.dumps(data))
    return p


def test_loads_a_valid_profile(tmp_path):
    profile = load_profile(write_profile(tmp_path))
    assert profile.name == "Example Artist"
    assert profile.works[0].title == "Untitled"
    assert profile.links[0].url == "https://example.com"


@pytest.mark.parametrize("field", ["name", "disciplines", "tagline", "consent"])
def test_missing_required_field_is_an_error(tmp_path, field):
    data = {k: v for k, v in VALID.items() if k != field}
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ProfileError, match=field):
        load_profile(p)


def test_unknown_discipline_is_an_error(tmp_path):
    with pytest.raises(ProfileError, match="interpretive-mime"):
        load_profile(write_profile(tmp_path, disciplines=["interpretive-mime"]))


def test_more_than_three_disciplines_is_an_error(tmp_path):
    too_many = ["painters", "sculptors", "writers", "filmmakers"]
    with pytest.raises(ProfileError, match="at most 3"):
        load_profile(write_profile(tmp_path, disciplines=too_many))


def test_empty_disciplines_is_an_error(tmp_path):
    with pytest.raises(ProfileError, match="at least 1"):
        load_profile(write_profile(tmp_path, disciplines=[]))


def test_more_than_twelve_works_is_an_error(tmp_path):
    works = [{"src": f"w{i}.jpg", "alt": "x"} for i in range(13)]
    with pytest.raises(ProfileError, match="at most 12"):
        load_profile(write_profile(tmp_path, works=works))


def test_tagline_over_ninety_characters_is_an_error(tmp_path):
    with pytest.raises(ProfileError, match="90"):
        load_profile(write_profile(tmp_path, tagline="x" * 91))


def test_non_http_link_is_an_error(tmp_path):
    bad = [{"label": "Bad", "url": "javascript:alert(1)"}]
    with pytest.raises(ProfileError, match="http"):
        load_profile(write_profile(tmp_path, links=bad))


def test_image_without_alt_text_is_an_error(tmp_path):
    with pytest.raises(ProfileError, match="alt"):
        load_profile(write_profile(tmp_path, works=[{"src": "w.jpg", "alt": ""}]))


def test_missing_consent_flag_is_an_error(tmp_path):
    consent = {"listed": True, "images": False, "date": "2026-07-26"}
    with pytest.raises(ProfileError, match="consent"):
        load_profile(write_profile(tmp_path, consent=consent))


def test_unknown_availability_status_is_an_error(tmp_path):
    availability = {"status": "maybe", "openTo": [], "updated": "2026-07-26"}
    with pytest.raises(ProfileError, match="status"):
        load_profile(write_profile(tmp_path, availability=availability))


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Example Artist", "example-artist"),
        ("  Padded  Name  ", "padded-name"),
        ("Ampersand & Co.", "ampersand-co"),
        ("Accented Ná", "accented-na"),
        ("Hyphen-Already", "hyphen-already"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_load_all_fills_missing_slugs_from_the_name(tmp_path):
    data = {**VALID}
    del data["slug"]
    (tmp_path / "a.json").write_text(json.dumps(data))
    profiles = load_all(tmp_path)
    assert profiles[0].slug == "example-artist"


def test_load_all_is_sorted_by_name(tmp_path):
    write_profile(tmp_path, slug="zoe", name="Zoe")
    write_profile(tmp_path, slug="adam", name="Adam")
    assert [p.name for p in load_all(tmp_path)] == ["Adam", "Zoe"]


def test_slug_collision_is_an_error(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({**VALID, "slug": "same"}))
    (tmp_path / "two.json").write_text(json.dumps({**VALID, "slug": "same"}))
    with pytest.raises(ProfileError, match="collision"):
        load_all(tmp_path)
