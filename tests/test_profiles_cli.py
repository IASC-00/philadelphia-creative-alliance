# ruff: noqa: S101
import json
from datetime import date
from pathlib import Path

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
    assert (root / "img" / "artists" / "example-artist").exists()
    write_profile(profiles, live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "example-artist").exists()
    assert not (root / "img" / "artists" / "example-artist").exists()


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


def _snapshot(root: Path) -> dict:
    """Every regular file under root, by relative path, with its exact bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_check_leaves_root_byte_for_byte_unchanged(workspace):
    """The load-bearing property behind fixing the healing bug: check() must
    never write anything under root, not just avoid getting fooled by a
    tampered file. A whole-tree snapshot proves this generally, rather than
    only for the specific swap/edit cases exercised elsewhere in this file."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    before = _snapshot(root)
    assert check(profiles, src, root, TODAY) == 0
    assert _snapshot(root) == before


def test_generate_does_not_prune_a_non_slug_directory_or_a_symlink(workspace, tmp_path):
    """The removal pass only touches slugs this tool has previously published
    (per the committed manifest). A hand-made directory that was never in
    that manifest, and a symlink (which `Path.is_dir()` would otherwise
    follow straight into a crashing `shutil.rmtree`), must both survive —
    proven with a live profile already on disk, so the removal pass has
    something real to walk past rather than finding an empty artists/."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0

    stray_dir = root / "artists" / "_shared"
    stray_dir.mkdir()
    (stray_dir / "keep.txt").write_text("keep me")

    link_target = tmp_path / "elsewhere"
    link_target.mkdir()
    (link_target / "file.txt").write_text("linked content")
    symlink = root / "artists" / "linked"
    symlink.symlink_to(link_target, target_is_directory=True)

    assert generate(profiles, src, root, TODAY) == 0
    assert stray_dir.exists()
    assert (stray_dir / "keep.txt").read_text() == "keep me"
    assert symlink.is_symlink()
    assert (symlink / "file.txt").read_text() == "linked content"


def test_generate_refuses_to_prune_when_no_profile_files_exist(workspace):
    """A profiles/ directory with zero *.json files in it is almost always a
    misconfigured path, not a deliberate 'unpublish everything.' Distinct
    from every profile being live:false (a real editorial choice captured in
    real files), which must still publish and prune correctly — see
    test_live_false_removes_a_previously_built_page."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()

    for f in profiles.iterdir():
        f.unlink()
    assert generate(profiles, src, root, TODAY) == 1
    assert (root / "artists" / "example-artist").exists()
    assert (root / "img" / "artists" / "example-artist").exists()


def test_generate_refuses_to_prune_when_profiles_dir_is_missing(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()

    missing = profiles.parent / "does-not-exist"
    assert generate(missing, src, root, TODAY) == 1
    assert (root / "artists" / "example-artist").exists()
    assert (root / "img" / "artists" / "example-artist").exists()


def test_check_fails_cleanly_on_a_corrupt_manifest(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    (root / "img" / "artists" / "manifest.json").write_text("not valid json{")
    assert check(profiles, src, root, TODAY) == 1


def test_check_fails_cleanly_when_a_manifest_entry_is_malformed(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    (root / "img" / "artists" / "manifest.json").write_text(
        json.dumps({"example-artist/index.html": "not-a-dict"})
    )
    assert check(profiles, src, root, TODAY) == 1


def test_generate_leaves_no_images_behind_when_a_later_profile_fails(workspace):
    """Images used to be written straight into root inside _build's
    per-profile loop, so a missing intake image on the *second* profile left
    the first profile's images on disk with no page or manifest entry for
    them — a partial publish. generate() now builds entirely into a scratch
    directory first and only moves anything into root once every profile has
    succeeded."""
    profiles, src, root = workspace
    write_profile(profiles, slug="first", name="First Artist")
    write_profile(
        profiles,
        slug="second",
        name="Second Artist",
        portrait=None,
        works=[{"src": "missing.jpg", "alt": "Nowhere to be found."}],
    )
    assert generate(profiles, src, root, TODAY) == 1
    assert not (root / "artists" / "first").exists()
    assert not (root / "img" / "artists" / "first").exists()
    assert not (root / "img" / "artists" / "manifest.json").exists()


# append to tests/test_profiles_cli.py
from tools.profiles import rewrite_block


HTML = """var PCA = {
    // GENERATED disciplines — tools/profiles.py rewrites this block
    disciplines: [
      { slug:'old', label:'Old', t:1 }
    ],
    // END GENERATED disciplines
    artists: []
  };"""


def test_rewrite_block_replaces_only_between_markers():
    out = rewrite_block(HTML, "disciplines", "      REPLACED")
    assert "REPLACED" in out
    assert "{ slug:'old'" not in out
    assert "var PCA = {" in out
    assert "artists: []" in out


def test_rewrite_block_is_idempotent():
    once = rewrite_block(HTML, "disciplines", "      REPLACED")
    assert rewrite_block(once, "disciplines", "      REPLACED") == once


def test_rewrite_block_errors_when_markers_are_absent():
    with pytest.raises(ValueError, match="marker"):
        rewrite_block("var PCA = {};", "disciplines", "x")


def test_generate_updates_index_html(workspace):
    profiles, src, root = workspace
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    updated = (root / "index.html").read_text()
    assert "digital-new-media" in updated
    assert "'example-artist'" in updated
    assert "/artists/example-artist/" in updated


ARTISTS_BLOCK = """// GENERATED artists — tools/profiles.py rewrites this block
    artists: [],
    // END GENERATED artists"""
