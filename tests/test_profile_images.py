# ruff: noqa: S101
import pytest
from PIL import Image

from tools.profile_images import (
    LONG_EDGE,
    fallback_tile,
    process_profile_images,
    resize_to_long_edge,
)
from tools.profile_schema import load_profile

from tests.test_profile_schema import VALID, write_profile


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    Image.new("RGB", (3000, 2000), (120, 60, 40)).save(d / "work-01.jpg")
    Image.new("RGB", (800, 900), (40, 90, 120)).save(d / "portrait.jpg")
    return d


def test_resize_bounds_the_long_edge():
    out = resize_to_long_edge(Image.new("RGB", (3000, 2000)), LONG_EDGE)
    assert out.size == (1600, 1067)


def test_resize_does_not_upscale_small_images():
    out = resize_to_long_edge(Image.new("RGB", (400, 300)), LONG_EDGE)
    assert out.size == (400, 300)


def test_processing_writes_webp_and_manifest_entries(tmp_path, src_dir):
    profile = load_profile(write_profile(tmp_path))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    written = tmp_path / "out" / "example-artist" / "work-01.webp"
    assert written.exists()
    assert entries["example-artist/work-01.webp"]["width"] == 1600
    assert len(entries["example-artist/work-01.webp"]["sha256"]) == 64


def test_processing_is_deterministic(tmp_path, src_dir):
    profile = load_profile(write_profile(tmp_path))
    first = process_profile_images(profile, src_dir, tmp_path / "a")
    second = process_profile_images(profile, src_dir, tmp_path / "b")
    assert first == second


def test_missing_source_image_is_an_error(tmp_path, src_dir):
    works = [{"src": "absent.jpg", "alt": "Not there."}]
    profile = load_profile(write_profile(tmp_path, works=works))
    with pytest.raises(FileNotFoundError, match="absent.jpg"):
        process_profile_images(profile, src_dir, tmp_path / "out")


def test_fallback_tile_is_deterministic_per_slug(tmp_path):
    profile = load_profile(write_profile(tmp_path))
    assert fallback_tile(profile).tobytes() == fallback_tile(profile).tobytes()


def test_fallback_tile_uses_the_first_discipline_hue(tmp_path):
    profile = load_profile(write_profile(tmp_path, disciplines=["painters"]))
    other = load_profile(
        write_profile(tmp_path, slug="b", name="B", disciplines=["sculptors"])
    )
    assert fallback_tile(profile).tobytes() != fallback_tile(other).tobytes()
