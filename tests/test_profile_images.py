# ruff: noqa: S101
import pytest
from PIL import Image

from tools.profile_images import (
    LONG_EDGE,
    fallback_tile,
    process_profile_images,
    resize_to_long_edge,
)
from tools.profile_schema import ProfileError, load_profile

from tests.test_profile_schema import write_profile


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


WALLS = {
    "example-artist/wall-wide.webp",
    "example-artist/wall-tall.webp",
    "example-artist/band.webp",
}


def test_process_profile_images_falls_back_to_tile_when_no_imagery_at_all(tmp_path):
    profile = load_profile(write_profile(tmp_path, works=[], portrait=None))
    entries = process_profile_images(profile, tmp_path, tmp_path / "out")
    assert set(entries.keys()) == {"example-artist/tile.webp"} | WALLS
    assert (tmp_path / "out" / "example-artist" / "tile.webp").exists()


def test_works_present_but_no_portrait_still_gets_a_header_tile(tmp_path, src_dir):
    profile = load_profile(write_profile(tmp_path, portrait=None))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    assert set(entries.keys()) == {
        "example-artist/work-01.webp",
        "example-artist/tile.webp",
    } | WALLS
    assert (tmp_path / "out" / "example-artist" / "work-01.webp").exists()
    assert (tmp_path / "out" / "example-artist" / "tile.webp").exists()


def test_a_profile_with_a_portrait_never_gets_a_tile(tmp_path, src_dir):
    profile = load_profile(write_profile(tmp_path))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    assert "example-artist/tile.webp" not in entries
    assert not (tmp_path / "out" / "example-artist" / "tile.webp").exists()


def test_every_profile_gets_its_banner_walls_at_their_sizes(tmp_path, src_dir):
    # The profile banner and closing band are generated for every member,
    # portrait or not: the page is laid out around them.
    profile = load_profile(write_profile(tmp_path))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    sizes = {k: (v["width"], v["height"]) for k, v in entries.items() if k in WALLS}
    assert sizes == {
        "example-artist/wall-wide.webp": (2400, 1000),
        "example-artist/wall-tall.webp": (1100, 1500),
        "example-artist/band.webp": (2400, 220),
    }
    for name in ("wall-wide", "wall-tall", "band"):
        assert (tmp_path / "out" / "example-artist" / f"{name}.webp").exists()


@pytest.mark.parametrize("stem", ["wall-wide", "wall-tall", "band"])
def test_a_work_named_like_a_generated_wall_collides(tmp_path, stem):
    works = [{"src": f"{stem}.jpg", "alt": "A piece with an unlucky name."}]
    profile = load_profile(write_profile(tmp_path, works=works))
    with pytest.raises(ProfileError, match=f"{stem}.webp"):
        process_profile_images(profile, tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_member_walls_take_the_first_discipline_hue(tmp_path):
    # Slug held constant (the seed), so only the hue can make these differ.
    bare = dict(works=[], portrait=None)
    painter = load_profile(write_profile(tmp_path, disciplines=["painters"], **bare))
    sculptor = load_profile(write_profile(tmp_path, disciplines=["sculptors"], **bare))
    a = process_profile_images(painter, tmp_path, tmp_path / "a")
    b = process_profile_images(sculptor, tmp_path, tmp_path / "b")
    for key in WALLS:
        assert a[key]["sha256"] != b[key]["sha256"]


def test_a_work_named_tile_collides_with_the_generated_fallback(tmp_path):
    # No portrait means the pipeline would generate tile.webp for the header
    # slot; a work whose stem is also "tile" would otherwise silently
    # overwrite it (or vice versa, depending on write order) instead of
    # raising. Neither the source image nor an out dir needs to exist for
    # this — the collision is detected from filenames alone, before any I/O.
    works = [{"src": "tile.jpg", "alt": "A piece literally named tile."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    with pytest.raises(ProfileError, match="tile.webp"):
        process_profile_images(profile, tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_two_works_sharing_a_filename_stem_collide(tmp_path):
    works = [
        {"src": "piece.jpg", "alt": "A piece."},
        {"src": "piece.png", "alt": "Another piece, different file."},
    ]
    profile = load_profile(write_profile(tmp_path, works=works))
    with pytest.raises(ProfileError, match="piece.webp"):
        process_profile_images(profile, tmp_path, tmp_path / "out")


def test_non_colliding_names_still_process_normally(tmp_path, src_dir):
    profile = load_profile(write_profile(tmp_path))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    assert set(entries.keys()) == {
        "example-artist/work-01.webp",
        "example-artist/portrait.webp",
    } | WALLS


def test_transparent_source_keeps_its_alpha_channel(tmp_path):
    d = tmp_path / "src2"
    d.mkdir()
    Image.new("RGBA", (800, 600), (200, 40, 30, 0)).save(d / "layer.png")
    works = [{"src": "layer.png", "alt": "A layered piece."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    process_profile_images(profile, d, tmp_path / "out")

    target = tmp_path / "out" / "example-artist" / "layer.webp"
    with Image.open(target) as saved:
        saved.load()
        assert saved.mode == "RGBA"
        pixel = saved.getpixel((0, 0))
        assert pixel != (0, 0, 0, 255)  # not flattened to an opaque black background
        assert pixel[3] < 255  # transparency survived, it isn't fully opaque


def test_grayscale_source_still_converts_to_rgb(tmp_path):
    d = tmp_path / "src3"
    d.mkdir()
    Image.new("L", (800, 600), 128).save(d / "sketch.jpg")
    works = [{"src": "sketch.jpg", "alt": "A pencil sketch."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    process_profile_images(profile, d, tmp_path / "out")

    target = tmp_path / "out" / "example-artist" / "sketch.webp"
    with Image.open(target) as saved:
        saved.load()
        assert saved.mode == "RGB"


def test_fallback_tile_is_deterministic_per_slug(tmp_path):
    profile = load_profile(write_profile(tmp_path))
    assert fallback_tile(profile).tobytes() == fallback_tile(profile).tobytes()


def test_fallback_tile_uses_the_first_discipline_hue(tmp_path):
    """The slug must be held constant. The member wall is seeded from the slug,
    so varying both slug and discipline (as this test used to) is satisfied by
    the slug difference alone — a hue-blind implementation passed it."""
    painter = load_profile(write_profile(tmp_path, disciplines=["painters"]))
    sculptor = load_profile(write_profile(tmp_path, disciplines=["sculptors"]))
    assert painter.slug == sculptor.slug
    assert fallback_tile(painter).tobytes() != fallback_tile(sculptor).tobytes()


# Exit-gate 8 Nov (M3): member-submitted files are opened only as the three
# formats the site publishes from. PSD, EPS, TIFF and BMP carry the worst of
# Pillow's parser advisories and are refused before any decoder runs.
@pytest.mark.parametrize("fmt,ext", [("TIFF", "tif"), ("BMP", "bmp"), ("GIF", "gif")])
def test_only_jpeg_png_and_webp_are_opened(tmp_path, fmt, ext):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (40, 40), (1, 2, 3)).save(src / f"work.jpg", format=fmt)
    works = [{"src": "work.jpg", "alt": "A work."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    with pytest.raises(ProfileError, match="JPEG, PNG or WebP"):
        process_profile_images(profile, src, tmp_path / "out")


def test_a_truncated_jpeg_is_a_named_error(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (400, 400), (9, 9, 9)).save(src / "work.jpg")
    data = (src / "work.jpg").read_bytes()
    (src / "work.jpg").write_bytes(data[: len(data) // 3])
    works = [{"src": "work.jpg", "alt": "A work."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    with pytest.raises(ProfileError, match="could not be read"):
        process_profile_images(profile, src, tmp_path / "out")


# Exit-gate 8 Nov, final pass (M1): every way a submitted file can fail to
# decode is a named ProfileError, never a traceback out of the CLI.
def _one_work_profile(tmp_path):
    works = [{"src": "work.jpg", "alt": "A work."}]
    return load_profile(write_profile(tmp_path, works=works, portrait=None))


def test_a_cut_short_webp_is_a_named_error(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (300, 300), (5, 6, 7)).save(src / "work.jpg", format="WEBP")
    data = (src / "work.jpg").read_bytes()
    (src / "work.jpg").write_bytes(data[: len(data) // 2])
    with pytest.raises(ProfileError, match="work.jpg"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


def test_a_thirty_byte_jpeg_is_a_named_error(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (300, 300), (5, 6, 7)).save(src / "work.jpg")
    (src / "work.jpg").write_bytes((src / "work.jpg").read_bytes()[:30])
    with pytest.raises(ProfileError, match="work.jpg"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


def test_a_directory_named_like_an_image_is_a_named_error(tmp_path):
    src = tmp_path / "src"; src.mkdir(); (src / "work.jpg").mkdir()
    with pytest.raises(ProfileError, match="work.jpg"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


def test_a_png_with_a_corrupt_chunk_is_a_named_error(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (64, 64), (5, 6, 7)).save(src / "work.jpg", format="PNG")
    data = bytearray((src / "work.jpg").read_bytes())
    i = data.index(b"IDAT") + 8; data[i:i + 8] = b"\x00" * 8
    (src / "work.jpg").write_bytes(bytes(data))
    with pytest.raises(ProfileError, match="work.jpg"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


def test_a_decompression_bomb_is_a_named_error(tmp_path, monkeypatch):
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (200, 200), (5, 6, 7)).save(src / "work.jpg")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)   # 40,000 px > 2x limit -> error
    with pytest.raises(ProfileError, match="work.jpg could not be read"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


def test_a_broken_png_syntax_error_is_a_named_error(tmp_path, monkeypatch):
    """Pillow reports some corrupt PNG chunks as SyntaxError while reading
    pixels (seen in the 8 Nov audit's fuzzing); that must not escape either."""
    import tools.profile_images as pi
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (64, 64), (5, 6, 7)).save(src / "work.jpg", format="PNG")
    def broken(*a, **k):
        raise SyntaxError("broken PNG file")
    monkeypatch.setattr(pi, "resize_to_long_edge", broken)
    with pytest.raises(ProfileError, match="could not be read"):
        process_profile_images(_one_work_profile(tmp_path), src, tmp_path / "out")


# Exit-gate 10 Nov (L2): macOS disks are case-insensitive by default, so
# Band.webp and band.webp are one file there.
@pytest.mark.parametrize("src", ["Band.jpg", "TILE.png", "Wall-Wide.jpg"])
def test_collisions_ignore_case(tmp_path, src):
    works = [{"src": src, "alt": "A piece with an unlucky name."}]
    profile = load_profile(write_profile(tmp_path, works=works, portrait=None))
    with pytest.raises(ProfileError, match="rename one source"):
        process_profile_images(profile, tmp_path, tmp_path / "out")
