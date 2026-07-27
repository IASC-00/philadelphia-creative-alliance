# ruff: noqa: S101
"""Byte-stability guard: the same input must always produce the same output."""

from datetime import date

from PIL import Image

from tools.profile_render import render_profile
from tools.profile_schema import load_profile
from tools.profiles import generate

from tests.test_profile_schema import write_profile

TODAY = date(2026, 7, 26)


def _workspace(tmp_path, name):
    profiles = tmp_path / name / "profiles"
    profiles.mkdir(parents=True)
    src = tmp_path / name / "intake"
    src.mkdir(parents=True)
    Image.new("RGB", (2000, 1500), (110, 70, 50)).save(src / "work-01.jpg")
    Image.new("RGB", (900, 900), (50, 80, 110)).save(src / "portrait.jpg")
    root = tmp_path / name / "site"
    (root / "artists").mkdir(parents=True)
    return profiles, src, root


def test_output_is_byte_stable_across_runs(tmp_path):
    outputs = []
    for name in ("first", "second"):
        profiles, src, root = _workspace(tmp_path, name)
        write_profile(profiles)
        assert generate(profiles, src, root, TODAY) == 0
        page = root / "artists" / "example-artist" / "index.html"
        image = root / "img" / "artists" / "example-artist" / "work-01.webp"
        outputs.append((page.read_text(), image.read_bytes()))
    assert outputs[0] == outputs[1]


def test_today_parameter_is_load_bearing(tmp_path):
    """The byte-stability test above cannot catch a reintroduced clock read,
    which is the property tools/profile_render.py cites it for: both runs
    happen in one process on one day, so a date-granularity clock read
    returns the same value in each and the comparison still passes. A
    reviewer mutated render_profile to ignore `today` and the whole suite
    stayed green.

    This is the clock-independent half. If `today` stops reaching the
    renderer, the two calls below can no longer differ — no matter what day
    the suite runs on."""
    avail = {"status": "open", "openTo": ["teaching"], "updated": "2026-07-26"}
    p = load_profile(write_profile(tmp_path, availability=avail))
    fresh = render_profile(p, date(2026, 7, 26))
    stale = render_profile(p, date(2026, 10, 26))
    assert fresh != stale
    assert "Taking work now" in fresh and "Taking work now" not in stale
