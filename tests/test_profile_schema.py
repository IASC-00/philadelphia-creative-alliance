# ruff: noqa: S101
import json
from datetime import date
from pathlib import Path

import pytest

from tools.profile_schema import (
    DISCIPLINES,
    DISCIPLINE_SLUGS,
    ProfileError,
    STALE_AFTER_DAYS,
    availability_is_current,
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


@pytest.mark.parametrize(
    "src",
    [
        "../originals/piece.jpg",
        "../../etc/passwd",
        "/etc/passwd",
        "sub/dir/piece.jpg",
        ".",
        "..",
    ],
)
def test_image_src_outside_intake_is_an_error(tmp_path, src):
    """`src` is joined onto intake/ verbatim by tools/profile_images.py, so a
    directory component reads an image from outside intake/ and publishes it
    into img/artists/<slug>/ — which is not gitignored, so it gets committed.
    The slug got a strict regex after ".." once overwrote the homepage; this
    is the same class of hand-transcribed value."""
    works = [{"src": src, "alt": "Somewhere it should not be."}]
    with pytest.raises(ProfileError, match="bare filename"):
        load_profile(write_profile(tmp_path, works=works))


def test_portrait_src_outside_intake_is_an_error(tmp_path):
    portrait = {"src": "../originals/me.jpg", "alt": "A portrait."}
    with pytest.raises(ProfileError, match="bare filename"):
        load_profile(write_profile(tmp_path, portrait=portrait))


@pytest.mark.parametrize(
    "src",
    [
        "..\\originals\\piece.jpg",
        "C:\\photos\\piece.jpg",
        "\\\\server\\share\\piece.jpg",
        "C:piece.jpg",
    ],
)
def test_windows_style_image_src_is_an_error(tmp_path, src):
    """On POSIX, Path(r"..\\originals\\piece.jpg").name is the whole string, so
    the separator check alone lets a pasted Windows path through. It then
    writes a hidden dotfile artefact and emits an <img src> that the WHATWG
    URL parser resolves with backslash-as-slash — landing outside the artist's
    directory and 404ing for every visitor, with --check green."""
    works = [{"src": src, "alt": "A pasted Windows path."}]
    with pytest.raises(ProfileError, match="bare filename"):
        load_profile(write_profile(tmp_path, works=works))


@pytest.mark.parametrize(
    "work,expected",
    [
        ({"src": 123, "alt": "ok"}, "src must be a string"),
        ({"src": "w.jpg", "alt": 5}, "alt must be a string"),
        ({"src": {"nested": True}, "alt": "ok"}, "src must be a string"),
    ],
)
def test_non_string_image_fields_raise_profile_error(tmp_path, work, expected):
    """A JSON number or object here used to raise TypeError/AttributeError out
    of this module. Callers that only catch ProfileError — tools/profiles.py's
    check() — turned that into an unhandled traceback rather than a named
    validation failure."""
    with pytest.raises(ProfileError, match=expected):
        load_profile(write_profile(tmp_path, works=[work]))


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


def test_non_latin_name_producing_empty_slug_is_an_error(tmp_path):
    data = {**VALID, "name": "日本語アーティスト"}
    del data["slug"]
    (tmp_path / "japanese.json").write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="empty slug"):
        load_all(tmp_path)


def test_punctuation_only_name_producing_empty_slug_is_an_error(tmp_path):
    data = {**VALID, "name": "!!!"}
    del data["slug"]
    (tmp_path / "punctuation.json").write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="empty slug"):
        load_all(tmp_path)


def test_whitespace_only_explicit_slug_is_an_error(tmp_path):
    write_profile(tmp_path, slug="   ")
    with pytest.raises(ProfileError, match="empty slug"):
        load_all(tmp_path)


def test_padded_explicit_slug_is_stripped(tmp_path):
    write_profile(tmp_path, slug="  jane-doe  ")
    profiles = load_all(tmp_path)
    assert profiles[0].slug == "jane-doe"


@pytest.mark.parametrize(
    "slug",
    [
        "..",
        "../escape",
        "jane/doe",
        "/etc/passwd",
        "Jane-Doe",
        "jane doe",
        "-leading-hyphen",
        "trailing-hyphen-",
    ],
)
def test_unsafe_or_malformed_explicit_slug_is_an_error(tmp_path, slug):
    """A slug becomes a filesystem path segment in tools/profiles.py
    (artists/<slug>/index.html, img/artists/<slug>/). An explicit slug of
    ".." previously wrote straight through to artists/../index.html —
    silently overwriting the site's homepage. write_profile() can't be used
    here since it names the file after the slug itself, which breaks for a
    slug containing "/"."""
    data = {**VALID, "slug": slug}
    (tmp_path / "profile.json").write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="slug"):
        load_all(tmp_path)


# --- availability staleness -------------------------------------------------

TODAY = date(2026, 7, 26)


def _with_updated(tmp_path, updated):
    availability = {"status": "open", "openTo": ["commissions"], "updated": updated}
    return load_profile(write_profile(tmp_path, availability=availability))


def test_fresh_availability_is_current(tmp_path):
    assert availability_is_current(_with_updated(tmp_path, "2026-07-26"), TODAY)


def test_exactly_ninety_days_old_is_still_current(tmp_path):
    assert availability_is_current(_with_updated(tmp_path, "2026-04-27"), TODAY)


def test_ninety_one_days_old_is_stale(tmp_path):
    assert not availability_is_current(_with_updated(tmp_path, "2026-04-26"), TODAY)


def test_unparseable_date_is_treated_as_stale(tmp_path):
    assert not availability_is_current(_with_updated(tmp_path, "soon"), TODAY)


def test_threshold_is_ninety():
    assert STALE_AFTER_DAYS == 90


def test_null_updated_is_treated_as_stale(tmp_path):
    availability = {"status": "open", "openTo": ["commissions"], "updated": None}
    profile = load_profile(write_profile(tmp_path, availability=availability))
    assert not availability_is_current(profile, TODAY)


def test_numeric_updated_is_treated_as_stale(tmp_path):
    availability = {"status": "open", "openTo": ["commissions"], "updated": 20260701}
    profile = load_profile(write_profile(tmp_path, availability=availability))
    assert not availability_is_current(profile, TODAY)


def test_a_draft_loads_without_consent(tmp_path):
    """A live:false draft is shown to the artist before they agree, so it has
    no consent to record yet. It must still load — and stay not-live."""
    data = {k: v for k, v in VALID.items() if k != "consent"}
    data["live"] = False
    p = tmp_path / "draft.json"
    p.write_text(json.dumps(data))
    profile = load_profile(p)
    assert profile.live is False
    assert profile.consent == {}


def test_flipping_a_draft_to_live_without_consent_fails(tmp_path):
    for live in (True, "false"):
        data = {k: v for k, v in VALID.items() if k != "consent"}
        data["live"] = live
        p = tmp_path / "draft.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ProfileError, match="consent"):
            load_profile(p)


def test_a_live_profile_with_partial_consent_still_fails(tmp_path):
    consent = {"listed": True, "images": True, "date": ""}
    with pytest.raises(ProfileError, match="consent"):
        load_profile(write_profile(tmp_path, consent=consent))


def test_a_live_profile_with_no_images_needs_no_image_consent(tmp_path):
    """A member can be listed before they send photos: the page then shows
    only the generated tile, so there is no image of theirs to consent to."""
    consent = {"listed": True, "date": "2026-11-08"}
    profile = load_profile(
        write_profile(tmp_path, consent=consent, works=[], portrait=None)
    )
    assert profile.live and profile.works == ()


def test_image_consent_is_still_required_once_a_work_is_shown(tmp_path):
    consent = {"listed": True, "date": "2026-11-08"}
    with pytest.raises(ProfileError, match="consent"):
        load_profile(write_profile(tmp_path, consent=consent, portrait=None))


def test_image_consent_is_still_required_for_a_portrait_alone(tmp_path):
    consent = {"listed": True, "date": "2026-11-08"}
    with pytest.raises(ProfileError, match="consent"):
        load_profile(write_profile(tmp_path, consent=consent, works=[]))
