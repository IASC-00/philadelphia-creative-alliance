# ruff: noqa: S101
import json
from datetime import date

import pytest
from PIL import Image

from tools.profiles import check, generate

from tests.test_profile_schema import write_profile

TODAY = date(2026, 7, 26)


@pytest.fixture
def workspace(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    src = tmp_path / "intake"
    src.mkdir()
    Image.new("RGB", (2000, 1500), (110, 70, 50)).save(src / "work-01.jpg")
    Image.new("RGB", (900, 900), (50, 80, 110)).save(src / "portrait.jpg")
    root = tmp_path / "site"
    (root / "artists").mkdir(parents=True)
    return profiles, src, root


def test_generate_writes_a_page_and_a_manifest(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist" / "index.html").exists()
    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert "example-artist/work-01.webp" in manifest


def test_check_passes_on_freshly_generated_output(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    assert check(profiles, src, root, TODAY) == 0


def test_check_fails_when_output_is_stale(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    page = root / "artists" / "example-artist" / "index.html"
    page.write_text(page.read_text().replace("Example Artist", "Edited By Hand"))
    assert check(profiles, src, root, TODAY) == 1


def test_check_fails_when_an_image_is_replaced(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    Image.new("RGB", (1600, 1200), (0, 0, 0)).save(
        root / "img" / "artists" / "example-artist" / "work-01.webp"
    )
    assert check(profiles, src, root, TODAY) == 1


def test_live_false_removes_a_previously_built_page(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    write_profile(profiles, live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "example-artist").exists()


def test_generate_fails_loudly_on_an_invalid_profile(workspace):
    profiles, src, root = workspace
    (profiles / "broken.json").write_text(json.dumps({"name": "No Consent"}))
    assert generate(profiles, src, root, TODAY) == 1


def test_generate_does_not_touch_a_stray_file_in_artists_dir(workspace):
    """The removal pass walks artists/ to prune pages for profiles that are no
    longer live. artists/profile.css lives in that same directory in the real
    site tree, so the pass must only ever remove directories, never files —
    proven here with a stray file rather than assumed."""
    profiles, src, root = workspace
    stylesheet = root / "artists" / "profile.css"
    stylesheet.write_text("/* shared stylesheet */")
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert stylesheet.exists()
    assert stylesheet.read_text() == "/* shared stylesheet */"


def test_generate_removes_a_stale_profile_directory_and_its_images(workspace):
    profiles, src, root = workspace
    write_profile(profiles, slug="first", name="First Artist")
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "first").exists()
    assert (root / "img" / "artists" / "first").exists()

    write_profile(profiles, slug="second", name="Second Artist")
    (profiles / "first.json").unlink()
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "first").exists()
    assert not (root / "img" / "artists" / "first").exists()
    assert (root / "artists" / "second").exists()


def test_check_fails_when_a_profile_was_edited_but_not_regenerated(workspace):
    """A naive --check that rebuilds straight into the real output tree would
    silently overwrite a stale page with the correct one before comparing —
    healing the very drift it exists to catch — and never notice that the
    profile source changed underneath a committed page. Proven here by
    editing the source tagline (not the output) and never calling generate()
    again."""
    profiles, src, root = workspace
    path = write_profile(profiles)
    generate(profiles, src, root, TODAY)
    data = json.loads(path.read_text())
    data["tagline"] = "A brand new tagline nobody has seen yet."
    path.write_text(json.dumps(data))
    assert check(profiles, src, root, TODAY) == 1


def test_check_fails_when_a_profile_was_removed_but_not_regenerated(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    (profiles / "example-artist.json").unlink()
    assert check(profiles, src, root, TODAY) == 1
