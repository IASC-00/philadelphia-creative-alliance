# ruff: noqa: S101
"""Byte-stability guard: the same input must always produce the same output."""

from datetime import date

from PIL import Image

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
