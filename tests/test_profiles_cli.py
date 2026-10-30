# ruff: noqa: S101
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

import tools.profiles as profiles_module
from tools.mosaic import ROOT
from tools.profile_schema import Availability, Discipline, DISCIPLINES, Profile
from tools.profiles import (
    _js_string,
    _swap_into_place,
    check,
    generate,
    preview,
    render_config_blocks,
    rewrite_block,
)

from tests.test_profile_schema import VALID, write_profile

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
    test_live_false_removes_a_previously_built_page.

    Zero profile files is not fatal to the whole run, though — only the
    *removal* pass is skipped (proven here by the previously published page
    and its images surviving). The taxonomy/roster rewrite is independent of
    that guard and still succeeds — see
    test_generate_with_no_profiles_still_rewrites_config_blocks."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()

    for f in profiles.iterdir():
        f.unlink()
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()
    assert (root / "img" / "artists" / "example-artist").exists()


def test_generate_refuses_to_prune_when_profiles_dir_is_missing(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()

    missing = profiles.parent / "does-not-exist"
    assert generate(missing, src, root, TODAY) == 0
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


# --- filesystem safety -----------------------------------------------------


def test_deleting_the_last_profile_record_keeps_its_manifest_entry(workspace):
    """The manifest is the only record of what has been published, and
    _previously_known_slugs reads it to decide what may ever be pruned.
    Overwriting it with the (empty) set produced by a run whose prune was
    skipped wiped that record: the member's page and photos stayed on disk,
    nothing could prune them afterwards, and the documented `live: false`
    takedown stopped working too — all while exiting 0."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0

    (profiles / "example-artist.json").unlink()
    assert generate(profiles, src, root, TODAY) == 0

    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert "example-artist/index.html" in manifest
    assert "example-artist/work-01.webp" in manifest


def test_the_skipped_prune_warning_names_the_takedown_procedure(workspace, capsys):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    (profiles / "example-artist.json").unlink()
    capsys.readouterr()

    assert generate(profiles, src, root, TODAY) == 0
    warning = capsys.readouterr().out
    assert '"live": false' in warning
    assert "Deleting the record instead leaves the published page" in warning


def _strand_a_page(profiles, root):
    """The exact state the wiped-manifest bug produced: a published page and
    its photos on disk, the member's record gone, and nothing in the manifest
    referring to either. Reached by hand here because the bug that produced
    it is fixed. With no record left there is nothing to render, so the
    page-staleness comparison has nothing to say — only the orphan guard can
    see this."""
    (profiles / "example-artist.json").unlink()
    (root / "img" / "artists" / "manifest.json").write_text("{}")


def test_check_fails_on_a_published_page_with_no_manifest_entry(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _strand_a_page(profiles, root)

    assert check(profiles, src, root, TODAY) == 1


def test_check_reports_the_orphan_by_name(workspace, capsys):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _strand_a_page(profiles, root)
    capsys.readouterr()

    check(profiles, src, root, TODAY)
    out = capsys.readouterr().out
    assert "artists/example-artist/index.html" in out
    assert "orphaned page" in out


def test_check_does_not_call_a_hand_made_directory_an_orphan(workspace):
    """The prune is forbidden to touch a directory it never published, so the
    orphan report must not flag one either — it is anchored on a published
    index.html, not on the directory existing."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    shared = root / "artists" / "_shared"
    shared.mkdir()
    (shared / "partial.html").write_text("<p>not a published page</p>")

    assert check(profiles, src, root, TODAY) == 0


def test_swap_into_place_replaces_the_directory_and_leaves_no_staging(tmp_path):
    built = tmp_path / "scratch" / "artist"
    built.mkdir(parents=True)
    (built / "new.webp").write_bytes(b"new")
    dest = tmp_path / "live" / "artist"
    dest.mkdir(parents=True)
    (dest / "old.webp").write_bytes(b"old")

    _swap_into_place(built, dest)

    assert (dest / "new.webp").read_bytes() == b"new"
    assert not (dest / "old.webp").exists()
    assert [p.name for p in sorted(dest.parent.iterdir())] == ["artist"]


def test_swap_into_place_keeps_the_live_directory_when_the_move_fails(
    tmp_path, monkeypatch
):
    """The move loop used to rmtree the destination and *then* move the
    replacement in, with no staging and no rollback. The scratch tree is a
    TemporaryDirectory, usually on another filesystem, so that move is a real
    copy — a failure partway through (or a Ctrl-C in the window) deleted an
    already-published artist's images and put nothing back."""
    built = tmp_path / "scratch" / "artist"
    built.mkdir(parents=True)
    (built / "new.webp").write_bytes(b"new")
    dest = tmp_path / "live" / "artist"
    dest.mkdir(parents=True)
    (dest / "old.webp").write_bytes(b"old")

    def no_space(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(profiles_module.shutil, "move", no_space)
    with pytest.raises(OSError, match="no space"):
        _swap_into_place(built, dest)

    assert (dest / "old.webp").read_bytes() == b"old"


BROKEN_ARTISTS_BLOCK = """// GENERATED artists — tools/profiles.py rewrites this block
    artists: [],"""


def test_a_failure_writing_index_html_leaves_the_published_page_in_place(workspace):
    """The prune used to run before anything was written, so any failure
    afterwards left the live site linking to pages that had already been
    deleted. Proven with the reviewer's case: an index.html whose end marker
    has been dropped, which rewrite_block raises on by design."""
    profiles, src, root = workspace
    write_profile(profiles, slug="first", name="First Artist")
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "first" / "index.html").exists()

    (profiles / "first.json").unlink()
    write_profile(profiles, slug="second", name="Second Artist")
    (root / "index.html").write_text(HTML.replace("artists: []", BROKEN_ARTISTS_BLOCK))

    with pytest.raises(ValueError, match="marker"):
        generate(profiles, src, root, TODAY)

    assert (root / "artists" / "first" / "index.html").exists()


def _two_members(profiles, src, root):
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles, slug="alpha", name="Alpha Artist")
    write_profile(profiles, slug="beta", name="Beta Artist")
    assert generate(profiles, src, root, TODAY) == 0
    return root / "index.html"


def _take_down(profiles, slug):
    data = json.loads((profiles / f"{slug}.json").read_text())
    data["live"] = False
    (profiles / f"{slug}.json").write_text(json.dumps(data))


def _take_beta_down(profiles):
    _take_down(profiles, "beta")


def test_a_marker_failure_writes_nothing_at_all(workspace):
    """index.html is resolved before anything is written. A missing marker is
    the one realistic raise in generate(), and it used to land *between* the
    manifest write and the prune."""
    profiles, src, root = workspace
    index = _two_members(profiles, src, root)
    manifest_path = root / "img" / "artists" / "manifest.json"
    before = manifest_path.read_bytes()

    _take_beta_down(profiles)
    index.write_text(index.read_text().replace("// END GENERATED artists", ""))
    with pytest.raises(ValueError, match="marker"):
        generate(profiles, src, root, TODAY)

    assert manifest_path.read_bytes() == before


def test_a_marker_failure_does_not_strand_the_takedown_forever(workspace):
    """The manifest is what `known` is rebuilt from next run. Writing it
    before the prune meant a raise in between permanently removed the member
    from the set the prune is ever allowed to touch: their page and photos
    stayed published through every future clean run, exit 0."""
    profiles, src, root = workspace
    index = _two_members(profiles, src, root)
    good_index = index.read_text()

    _take_beta_down(profiles)
    index.write_text(good_index.replace("// END GENERATED artists", ""))
    with pytest.raises(ValueError, match="marker"):
        generate(profiles, src, root, TODAY)

    # the operator fixes index.html and re-runs; the takedown must complete
    index.write_text(good_index)
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "beta").exists()
    assert not (root / "img" / "artists" / "beta").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_a_failure_inside_the_prune_does_not_strand_the_takedown(
    workspace, monkeypatch
):
    """The manifest is written last, so it is the run's commit point. If the
    prune itself dies partway — a permissions change, a full disk — the old
    manifest must survive intact, because it is what `known` is rebuilt from
    next run. Writing it first meant a prune that failed once could never be
    retried: the member was already gone from `known`."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    _take_beta_down(profiles)

    real_rmtree = profiles_module.shutil.rmtree

    def denied(path, *args, **kwargs):
        # Only the prune's own removal fails; the image swap earlier in the
        # run must still succeed, or this never reaches the prune at all.
        if Path(path).name == "beta":
            raise PermissionError("cannot remove")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(profiles_module.shutil, "rmtree", denied)
    with pytest.raises(PermissionError):
        generate(profiles, src, root, TODAY)
    monkeypatch.undo()

    # the obstruction clears and the operator re-runs; the takedown completes
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "beta").exists()
    assert not (root / "img" / "artists" / "beta").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_a_failed_swap_leaves_no_staging_directory_behind(tmp_path, monkeypatch):
    """A partial .<slug>.incoming under img/artists/ is published — img/ is
    not gitignored — so it must not outlive the failure that created it."""
    built = tmp_path / "scratch" / "artist"
    built.mkdir(parents=True)
    (built / "new.webp").write_bytes(b"new")
    dest = tmp_path / "live" / "artist"
    dest.mkdir(parents=True)
    (dest / "old.webp").write_bytes(b"old")

    def half_copied(_src, dst, *_a, **_k):
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "partial.webp").write_bytes(b"half")
        raise OSError("no space left on device")

    monkeypatch.setattr(profiles_module.shutil, "move", half_copied)
    with pytest.raises(OSError, match="no space"):
        _swap_into_place(built, dest)

    assert (dest / "old.webp").read_bytes() == b"old"
    assert [p.name for p in sorted(dest.parent.iterdir())] == ["artist"]


def test_the_prune_removes_a_leftover_staging_directory(workspace):
    """Staging was only ever cleared by a later swap of the same slug, so a
    member taken down after an interrupted build kept their artwork served
    from img/artists/.<slug>.incoming/ indefinitely."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists"
    leftover = images / ".beta.incoming"
    leftover.mkdir()
    (leftover / "work-01.webp").write_bytes(
        (images / "beta" / "work-01.webp").read_bytes()
    )

    _take_beta_down(profiles)
    assert generate(profiles, src, root, TODAY) == 0

    assert not leftover.exists()
    assert [p for p in images.rglob("*.webp") if p.name.startswith("beta")] == []
    assert check(profiles, src, root, TODAY) == 0


def test_check_reports_an_orphaned_image_directory(workspace, capsys):
    """Removing an orphaned page by hand leaves the photos published and the
    gate green — so the de-listed member's work is still being served."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _strand_a_page(profiles, root)
    shutil.rmtree(root / "artists" / "example-artist")  # the page, removed by hand
    capsys.readouterr()

    assert check(profiles, src, root, TODAY) == 1
    out = capsys.readouterr().out
    assert "img/artists/example-artist/" in out
    assert "orphaned image directory" in out


# --- the prune reaches a member's images even without their page ------------
#
# The prune used to walk artists/ and key everything else off the page
# directory it found there, so once that directory was gone — removed by hand
# on this tool's own advice, by a prune that failed after it, or by a SIGKILL
# mid-run — the same member's photographs were unreachable forever. Every
# route the tool printed was a no-op and only `rm -rf` cleared the gate. It is
# driven by the manifest now: what this tool published is what it can prune.


def test_a_hand_removed_page_does_not_strand_the_members_images(workspace):
    """The de-listing that matters: a member is taken down with
    `live: false` while their page directory is already gone. Their images
    must still come down on the next run."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists" / "beta"
    assert images.exists()

    _take_beta_down(profiles)
    shutil.rmtree(root / "artists" / "beta")  # the page, removed by hand

    assert generate(profiles, src, root, TODAY) == 0
    assert not images.exists()
    assert check(profiles, src, root, TODAY) == 0


def test_the_documented_hand_removal_converges_on_the_next_run(workspace):
    """The reviewer's route A, end to end: the operator deletes the record,
    the generator warns and skips the prune, they remove the page by hand as
    the warning used to suggest, and a later run — here because another member
    joins — must take the photographs down rather than leave them served with
    the gate red and no working remedy."""
    profiles, src, root = workspace
    write_profile(profiles, slug="beta", name="Beta Artist")
    assert generate(profiles, src, root, TODAY) == 0

    (profiles / "beta.json").unlink()
    assert generate(profiles, src, root, TODAY) == 0  # warns, skips the prune
    shutil.rmtree(root / "artists" / "beta")

    write_profile(profiles, slug="alpha", name="Alpha Artist")
    assert generate(profiles, src, root, TODAY) == 0

    assert not (root / "img" / "artists" / "beta").exists()
    assert [
        p for p in (root / "img" / "artists").rglob("*.webp") if "beta" in str(p)
    ] == []
    assert check(profiles, src, root, TODAY) == 0


def test_a_prune_failure_on_the_images_leaves_the_page_behind(workspace, monkeypatch):
    """Images before the page, so a failure between the two halves leaves the
    recoverable half. Losing the page first left photographs with no page and
    (before the prune read the manifest) no way to reach them again.

    The patch is narrowed to the exact rmtree call being broken — patching
    rmtree by directory *name* matches artists/beta first and so never
    exercises this window at all."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    _take_beta_down(profiles)

    target = root / "img" / "artists" / "beta"
    real_rmtree = profiles_module.shutil.rmtree

    def denied(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError("cannot remove")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(profiles_module.shutil, "rmtree", denied)
    with pytest.raises(PermissionError):
        generate(profiles, src, root, TODAY)
    monkeypatch.undo()

    assert (root / "artists" / "beta" / "index.html").exists()

    # the obstruction clears and the operator re-runs; the takedown completes
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "beta").exists()
    assert not target.exists()
    assert check(profiles, src, root, TODAY) == 0


def test_a_prune_failure_on_one_slug_does_not_block_another(workspace, monkeypatch):
    """One member's unremovable directory must not keep a second member
    published: the failure is reported, the loop continues, and the run still
    fails loudly afterwards so the manifest keeps listing what is still on
    disk."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    _take_down(profiles, "alpha")
    _take_down(profiles, "beta")

    stuck = root / "img" / "artists" / "alpha"
    real_rmtree = profiles_module.shutil.rmtree

    def denied(path, *args, **kwargs):
        if Path(path) == stuck:
            raise PermissionError("cannot remove")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(profiles_module.shutil, "rmtree", denied)
    with pytest.raises(PermissionError):
        generate(profiles, src, root, TODAY)
    monkeypatch.undo()

    assert not (root / "artists" / "beta").exists()
    assert not (root / "img" / "artists" / "beta").exists()
    assert (root / "artists" / "alpha" / "index.html").exists()

    assert generate(profiles, src, root, TODAY) == 0
    assert check(profiles, src, root, TODAY) == 0


def test_the_prune_removes_leftover_staging_when_the_page_is_gone(workspace):
    """Staging is reached through the same manifest entry as the images, so a
    `.<slug>.incoming` that appears (or survives a SIGKILL) after the page is
    gone is still removed."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists"
    leftover = images / ".beta.incoming"
    leftover.mkdir()
    (leftover / "work-01.webp").write_bytes(
        (images / "beta" / "work-01.webp").read_bytes()
    )

    _take_beta_down(profiles)
    shutil.rmtree(root / "artists" / "beta")

    assert generate(profiles, src, root, TODAY) == 0
    assert not leftover.exists()
    assert not (images / "beta").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_a_non_directory_where_the_images_belong_fails_loudly(workspace, capsys):
    """The prune must never treat "I refused to remove this" as "it was
    already gone". Reporting nothing here would be followed by a manifest
    write that no longer names the member — and then no later run could reach
    them, while their bytes stayed served and --check passed green.

    Raising instead leaves the committed manifest intact and the gate red,
    which is what makes the state recoverable."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists" / "beta"
    shutil.rmtree(images)
    images.write_bytes(b"served bytes")
    _take_beta_down(profiles)

    with pytest.raises(OSError, match="(?i)not a directory"):
        generate(profiles, src, root, TODAY)

    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert any(key.startswith("beta/") for key in manifest)
    assert (root / "artists" / "beta" / "index.html").exists()
    assert check(profiles, src, root, TODAY) == 1

    # the operator clears the obstruction and re-runs; the takedown completes
    images.unlink()
    capsys.readouterr()
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "beta").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_a_symlink_where_the_images_belong_fails_loudly(workspace, tmp_path):
    """The same silent forgetting, reached through a symlink. `shutil.rmtree`
    raises on one, so the prune has always refused — but refusing quietly and
    then dropping the slug from the manifest is how a member stays served
    with the gate green forever."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists" / "beta"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "work-01.webp").write_bytes((images / "work-01.webp").read_bytes())
    shutil.rmtree(images)
    images.symlink_to(elsewhere, target_is_directory=True)
    _take_beta_down(profiles)

    with pytest.raises(OSError, match="symlink"):
        generate(profiles, src, root, TODAY)

    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert any(key.startswith("beta/") for key in manifest)
    assert images.is_symlink()
    assert (elsewhere / "work-01.webp").exists()
    assert check(profiles, src, root, TODAY) == 1


def test_a_partial_prune_still_reports_what_it_removed(workspace, capsys, monkeypatch):
    """A removal that did happen is reported as having happened, even when a
    later step for the same slug fails."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists"
    leftover = images / ".beta.incoming"
    leftover.mkdir()
    (leftover / "work-01.webp").write_bytes(
        (images / "beta" / "work-01.webp").read_bytes()
    )
    _take_beta_down(profiles)

    real_rmtree = profiles_module.shutil.rmtree

    def denied(path, *args, **kwargs):
        if Path(path) == leftover:
            raise PermissionError("cannot remove")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(profiles_module.shutil, "rmtree", denied)
    capsys.readouterr()
    with pytest.raises(PermissionError):
        generate(profiles, src, root, TODAY)
    out = capsys.readouterr().out
    monkeypatch.undo()

    assert "removed img/artists/beta" in out
    assert "FAILED to remove beta" in out
    # the page is the half a re-run rebuilds, so it must be the half left
    assert (root / "artists" / "beta" / "index.html").exists()


def test_the_prune_never_acts_on_a_manifest_key_that_escapes_the_tree(workspace):
    """The slugs the prune may remove now come from manifest *text* joined
    onto root, rather than from matching what is already in artists/. A
    hand-edited or corrupted manifest must not be able to name `../..`, so the
    same slug regex tools/profile_schema.py enforces on load is applied here
    before anything is joined or removed."""
    profiles, src, root = workspace
    write_profile(profiles)  # a live profile, so the prune runs at all
    assert generate(profiles, src, root, TODAY) == 0

    outsider = root.parent / "outside"
    outsider.mkdir()
    (outsider / "keep.txt").write_text("keep me")

    manifest_path = root / "img" / "artists" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["../../outside/index.html"] = {"sha256": "0" * 64}
    manifest_path.write_text(json.dumps(manifest))

    assert generate(profiles, src, root, TODAY) == 0
    assert (outsider / "keep.txt").read_text() == "keep me"


def test_an_unusable_manifest_key_is_reported_not_dropped_in_silence(workspace, capsys):
    """Skipping the key is the right thing to do with the filesystem; doing it
    without a word is not, since the manifest this run writes will no longer
    contain it."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    manifest_path = root / "img" / "artists" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["../../outside/index.html"] = {"sha256": "0" * 64}
    manifest_path.write_text(json.dumps(manifest))
    capsys.readouterr()

    assert generate(profiles, src, root, TODAY) == 0
    assert "ignored manifest entry" in capsys.readouterr().out


def test_restoring_a_deleted_record_as_not_live_completes_the_takedown(workspace):
    """What the skipped-prune warning now names as the way to finish, proven
    rather than asserted: with no profile files left the prune is skipped by
    design, and restoring the record with `live: false` is what removes the
    page and the images together."""
    profiles, src, root = workspace
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    (profiles / "example-artist.json").unlink()
    assert generate(profiles, src, root, TODAY) == 0

    write_profile(profiles, live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert not (root / "artists" / "example-artist").exists()
    assert not (root / "img" / "artists" / "example-artist").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_the_skipped_prune_warning_does_not_offer_a_half_takedown(workspace, capsys):
    """The warning used to end "until the record is restored or the page is
    removed by hand." Removing the page by hand is the route into the state
    this whole section is about: it takes the page down and leaves the
    photographs served.

    It also said --check would report the page as an orphan, which it does
    not: the manifest is carried forward here, so the entries are still in it
    and the failure reads "in the committed manifest but no longer produced."
    """
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    (profiles / "example-artist.json").unlink()
    capsys.readouterr()

    assert generate(profiles, src, root, TODAY) == 0
    warning = capsys.readouterr().out
    assert "by hand" not in warning
    assert "orphan" not in warning
    assert '"live": false' in warning

    # what the run actually reports in this state, and what the warning now
    # says instead
    assert check(profiles, src, root, TODAY) == 1
    assert "in the committed manifest but no longer produced" in capsys.readouterr().out


# --- --check names a remedy that actually works -----------------------------


def test_check_does_not_call_a_hand_made_image_directory_an_orphan(workspace):
    """The image half of the orphan guard needs the same exemption the page
    half has. The prune is forbidden to touch a directory it never published,
    so reporting a hand-made img/artists/_shared/ was a red gate with no
    action behind it."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    shared = root / "img" / "artists" / "_shared"
    shared.mkdir()
    (shared / "notes.txt").write_text("not published imagery")

    assert check(profiles, src, root, TODAY) == 0


def test_check_still_reports_a_hand_made_directory_holding_published_imagery(workspace):
    """The exemption is anchored on what this tool publishes — a WebP —
    exactly as the page half is anchored on a published index.html. A
    directory with a member's photographs in it is still reported however it
    is named."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    shared = root / "img" / "artists" / "_shared"
    shared.mkdir()
    (shared / "work-01.webp").write_bytes(
        (root / "img" / "artists" / "example-artist" / "work-01.webp").read_bytes()
    )

    assert check(profiles, src, root, TODAY) == 1


def test_check_tells_the_operator_to_re_run_for_leftover_staging(workspace, capsys):
    """Leftover staging for a live member clears on the next publish, not by
    setting `live: false` — which would take a member down who never asked
    to come down, and is not what removes it."""
    profiles, src, root = workspace
    _two_members(profiles, src, root)
    images = root / "img" / "artists"
    leftover = images / ".beta.incoming"
    leftover.mkdir()
    (leftover / "work-01.webp").write_bytes(
        (images / "beta" / "work-01.webp").read_bytes()
    )
    capsys.readouterr()

    assert check(profiles, src, root, TODAY) == 1
    out = capsys.readouterr().out
    assert "img/artists/.beta.incoming/" in out
    assert "Re-run the generator" in out
    assert '"live": false' not in out

    # and the named remedy is the one that works
    assert generate(profiles, src, root, TODAY) == 0
    assert not leftover.exists()
    assert check(profiles, src, root, TODAY) == 0


def test_the_staging_remedy_does_not_name_a_prune_that_cannot_run(workspace, capsys):
    """`generate()` skips the prune entirely when no profile records remain,
    so "re-run the generator" is false in exactly the state a one-member
    alliance lands in when that member asks to come down. The action that
    does work is putting the record back with `live: false`."""
    profiles, src, root = workspace
    write_profile(profiles, slug="beta", name="Beta Artist")
    generate(profiles, src, root, TODAY)
    images = root / "img" / "artists"
    leftover = images / ".beta.incoming"
    leftover.mkdir()
    (leftover / "work-01.webp").write_bytes(
        (images / "beta" / "work-01.webp").read_bytes()
    )
    (profiles / "beta.json").unlink()
    capsys.readouterr()

    assert check(profiles, src, root, TODAY) == 1
    out = capsys.readouterr().out
    assert "img/artists/.beta.incoming/" in out
    assert "a re-run will not clear this" in out

    # the instruction the other branch prints, run here: it does nothing
    assert generate(profiles, src, root, TODAY) == 0
    assert leftover.exists()

    # the instruction this branch prints: it works
    write_profile(profiles, slug="beta", name="Beta Artist", live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert not leftover.exists()
    assert not (images / "beta").exists()
    assert check(profiles, src, root, TODAY) == 0


def test_check_reports_unaccounted_imagery_whatever_its_format(workspace):
    """The exemption asks whether a directory holds imagery a visitor is
    served, not whether it holds this tool's own output format. A directory of
    JPEGs under img/artists/ is served exactly as WebP is."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    ghost = root / "img" / "artists" / "ghost-member"
    ghost.mkdir()
    Image.new("RGB", (400, 300), (9, 9, 9)).save(ghost / "photo.jpg")

    assert check(profiles, src, root, TODAY) == 1

    # and case does not launder it
    (ghost / "photo.jpg").rename(ghost / "PHOTO.JPG")
    assert check(profiles, src, root, TODAY) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_check_still_reports_when_a_directory_cannot_be_read(workspace, capsys):
    """The exemption reads each directory to decide, from inside --check's
    failure collection. An unreadable one must not abandon the report: the
    failures already found would never be printed, so the operator would see
    a traceback and no findings at all."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    page = root / "artists" / "example-artist" / "index.html"
    page.write_text(page.read_text().replace("Example Artist", "Edited By Hand"))
    locked = root / "img" / "artists" / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)  # noqa: S103 -- deliberately unreadable, restored below
    capsys.readouterr()

    try:
        assert check(profiles, src, root, TODAY) == 1
        out = capsys.readouterr().out
    finally:
        os.chmod(locked, 0o755)  # noqa: S103 -- so tmp_path teardown can clean up

    assert "does not match manifest" in out
    assert "img/artists/locked/" in out


def test_the_orphaned_image_message_does_not_promise_a_no_op(workspace, capsys):
    """An orphaned image directory is by definition one the manifest has no
    record of, and the prune may never touch what the manifest does not know
    about — so `live: false` plus a re-run provably does nothing here, and
    the guard must not name it."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _strand_a_page(profiles, root)
    shutil.rmtree(root / "artists" / "example-artist")
    capsys.readouterr()

    assert check(profiles, src, root, TODAY) == 1
    out = capsys.readouterr().out
    assert "orphaned image directory" in out
    assert "by hand" in out
    assert '"live": false' not in out

    # the action the message used to name, run: it changes nothing
    write_profile(profiles, live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "img" / "artists" / "example-artist").exists()


def test_the_orphaned_page_message_does_not_promise_a_no_op(workspace, capsys):
    """The same falsehood on the page half: nothing in the manifest records
    the page as published, so no `live: false` re-run can prune it."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _strand_a_page(profiles, root)
    capsys.readouterr()

    assert check(profiles, src, root, TODAY) == 1
    out = capsys.readouterr().out
    assert "orphaned page" in out
    assert "by hand" in out
    assert '"live": false' not in out

    write_profile(profiles, live=False)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist" / "index.html").exists()


def test_one_profiles_missing_originals_do_not_disable_image_checks_for_another(
    workspace,
):
    """`absent` used to be all-or-nothing, so a single missing original made
    --check skip image verification for *every* artist — on the authoring
    machine, where it is fully capable for all the others, while printing a
    NOTE reassuring the operator it was expected.

    The drift here is an edited *original* that was never regenerated. That is
    detectable only by rebuilding: the committed WebP still matches its own
    manifest hash, so the tamper pass sees nothing wrong."""
    profiles, src, root = workspace
    Image.new("RGB", (1200, 900), (10, 120, 60)).save(src / "beta-only.jpg")
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(
        profiles,
        slug="alpha",
        name="Alpha Artist",
        portrait=None,
        works=[{"src": "work-01.jpg", "alt": "Alpha's work."}],
    )
    write_profile(
        profiles,
        slug="beta",
        name="Beta Artist",
        portrait=None,
        works=[{"src": "beta-only.jpg", "alt": "Beta's work."}],
    )
    assert generate(profiles, src, root, TODAY) == 0
    assert check(profiles, src, root, TODAY) == 0

    # alpha re-crops their original and forgets to regenerate
    Image.new("RGB", (2000, 1500), (200, 30, 30)).save(src / "work-01.jpg")
    assert check(profiles, src, root, TODAY) == 1

    # beta's original is simply not on this machine — alpha's staleness must
    # still be caught, since alpha's own original is right here
    (src / "beta-only.jpg").unlink()
    assert check(profiles, src, root, TODAY) == 1


# --- --check away from the authoring machine -------------------------------
#
# intake/ is gitignored on purpose: only the processed WebP is published. So
# on a fresh clone or a CI runner the originals are simply not there, and an
# image rebuild is not available. That must not take the *page* comparison
# down with it — page HTML is a function of the committed profile JSON alone.


def _simulate_fresh_clone(src):
    """Everything a clone has (profiles/, artists/, img/artists/, the
    manifest) with the one thing it never has: intake/."""
    for f in src.iterdir():
        f.unlink()


def test_check_passes_on_a_fresh_clone_with_no_intake(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _simulate_fresh_clone(src)

    assert check(profiles, src, root, TODAY) == 0


def test_check_says_so_when_it_cannot_rebuild_the_images(workspace, capsys):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _simulate_fresh_clone(src)
    capsys.readouterr()

    check(profiles, src, root, TODAY)
    out = capsys.readouterr().out
    assert "intake/ is gitignored" in out
    assert "Page HTML is rebuilt and compared in full for every profile" in out


def test_check_still_catches_text_staleness_with_no_intake(workspace):
    """The whole point of splitting the two comparisons: with the originals
    absent, a profile edited but not regenerated used to be undetectable —
    the rebuild raised FileNotFoundError before rendering a single page."""
    profiles, src, root = workspace
    path = write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _simulate_fresh_clone(src)

    data = json.loads(path.read_text())
    data["tagline"] = "A brand new tagline nobody has seen yet."
    path.write_text(json.dumps(data))

    assert check(profiles, src, root, TODAY) == 1


def test_check_still_catches_a_swapped_image_with_no_intake(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    _simulate_fresh_clone(src)
    Image.new("RGB", (1600, 1200), (0, 0, 0)).save(
        root / "img" / "artists" / "example-artist" / "work-01.webp"
    )

    assert check(profiles, src, root, TODAY) == 1


def test_check_still_catches_a_removed_profile_with_no_intake(workspace):
    profiles, src, root = workspace
    write_profile(profiles, slug="first", name="First Artist")
    write_profile(profiles, slug="second", name="Second Artist")
    generate(profiles, src, root, TODAY)
    _simulate_fresh_clone(src)
    (profiles / "first.json").unlink()

    assert check(profiles, src, root, TODAY) == 1


# --- --check covers index.html's generated blocks ---------------------------


def _index_with_markers(root):
    index = root / "index.html"
    index.write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    return index


def test_check_fails_when_the_artist_roster_was_hand_edited(workspace):
    """The realistic path is a dropped merge hunk, not tampering: an artist
    left invisible on the wall, or linked to a 404, while --check stayed
    green because the manifest covers only artists/* pages and images."""
    profiles, src, root = workspace
    index = _index_with_markers(root)
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    assert check(profiles, src, root, TODAY) == 0

    index.write_text(index.read_text().replace("Example Artist", "Somebody Else"))
    assert check(profiles, src, root, TODAY) == 1


def test_check_fails_when_index_html_was_reverted_wholesale(workspace):
    profiles, src, root = workspace
    index = _index_with_markers(root)
    before = index.read_text()
    write_profile(profiles)
    generate(profiles, src, root, TODAY)

    index.write_text(before)
    assert check(profiles, src, root, TODAY) == 1


def test_check_names_the_block_that_does_not_match(workspace, capsys):
    profiles, src, root = workspace
    index = _index_with_markers(root)
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    index.write_text(index.read_text().replace("Example Artist", "Somebody Else"))
    capsys.readouterr()

    check(profiles, src, root, TODAY)
    out = capsys.readouterr().out
    assert "index.html: the generated 'artists' block" in out


def test_check_fails_when_a_marker_was_dropped_from_index_html(workspace):
    profiles, src, root = workspace
    index = _index_with_markers(root)
    write_profile(profiles)
    generate(profiles, src, root, TODAY)

    index.write_text(index.read_text().replace("// END GENERATED artists", ""))
    assert check(profiles, src, root, TODAY) == 1


# --- crossing the availability window ---------------------------------------

SOON = date(2026, 7, 27)  # inside the window: same output as TODAY
LATER = date(2026, 12, 31)  # past it: the badge stops rendering


def test_generate_records_the_date_it_rendered_on(workspace):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert manifest["_meta"]["generated"] == TODAY.isoformat()


def test_a_rebuild_that_changes_nothing_keeps_the_recorded_date(workspace):
    """The recorded date describes the bytes on disk. When a rebuild
    reproduces them exactly, the old date still reproduces them and is kept —
    otherwise every build would rewrite the manifest with today's date and
    dirty the tree for no change in output."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    manifest_path = root / "img" / "artists" / "manifest.json"
    before = manifest_path.read_bytes()

    assert generate(profiles, src, root, SOON) == 0
    assert manifest_path.read_bytes() == before


def test_a_rebuild_after_the_window_closes_records_the_new_date(workspace):
    """The converse, and the reason the date is recorded at all: past the
    window the badge stops rendering, so the page genuinely changes and the
    recorded date must move with it."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    page = root / "artists" / "example-artist" / "index.html"
    assert "Taking work now" in page.read_text()

    assert generate(profiles, src, root, LATER) == 0
    assert "Taking work now" not in page.read_text()
    manifest = json.loads((root / "img" / "artists" / "manifest.json").read_text())
    assert manifest["_meta"]["generated"] == LATER.isoformat()
    assert check(profiles, src, root, LATER) == 0


def test_check_passes_after_the_availability_window_closes(workspace):
    """The badge renders only while availability.updated is inside the 90-day
    window, so on day 91 a rebuild against *today* diverges from committed
    output with nothing on disk having changed. check() re-renders against
    the date recorded at generate time instead, which keeps the page a static
    artifact and the comparison reproducible."""
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)

    assert check(profiles, src, root, LATER) == 0


def test_check_reports_stale_availability_as_a_notice_not_a_failure(workspace, capsys):
    profiles, src, root = workspace
    write_profile(profiles)
    generate(profiles, src, root, TODAY)
    capsys.readouterr()

    assert check(profiles, src, root, LATER) == 0
    out = capsys.readouterr().out
    assert "NOTICE: example-artist" in out
    assert "last confirmed more than 90 days ago" in out
    assert "FAILED" not in out


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


def _artists_block_of(html: str) -> str:
    """Only the text between the artists markers.

    Asserting against the whole file is what let the multi-discipline
    contract go untested: `"digital-new-media" in updated` is satisfied by
    the taxonomy block, which always contains all 14 slugs no matter what the
    artist entry says."""
    return html.split("// GENERATED artists", 1)[1].split(
        "// END GENERATED artists", 1
    )[0]


def test_generate_updates_index_html(workspace):
    profiles, src, root = workspace
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles, disciplines=["digital-new-media", "painters"])
    generate(profiles, src, root, TODAY)
    updated = (root / "index.html").read_text()
    block = _artists_block_of(updated)

    assert '"example-artist"' in block
    # The roster carries an array of every discipline the member picked, in
    # the order they picked them -- not a single singular `discipline` field.
    assert 'disciplines:["digital-new-media", "painters"]' in block
    # "/" is emitted as the JS/JSON escape "\/" so a member-supplied value can
    # never spell out this generator's own "// END GENERATED" marker.
    assert _json_literal_for(block, "url") == "/artists/example-artist/"


ARTISTS_BLOCK = """// GENERATED artists — tools/profiles.py rewrites this block
    artists: [],
    // END GENERATED artists"""


def test_generate_with_no_profiles_still_rewrites_config_blocks(workspace):
    """The zero-profile-files guard must only skip the removal pass, not the
    whole run: the discipline taxonomy comes from DISCIPLINES alone (not
    from whether any artist has joined), and an empty artist roster is a
    legitimate state, not a reason to abort. Proven against a previously
    published page to also confirm the guard still does its original job —
    that page and its images must survive untouched."""
    profiles, src, root = workspace
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles)
    assert generate(profiles, src, root, TODAY) == 0
    assert (root / "artists" / "example-artist").exists()
    assert (root / "img" / "artists" / "example-artist").exists()

    (profiles / "example-artist.json").unlink()
    assert generate(profiles, src, root, TODAY) == 0

    # the removal pass is skipped, so the previously published page and its
    # images are untouched even though no profile files remain
    assert (root / "artists" / "example-artist").exists()
    assert (root / "img" / "artists" / "example-artist").exists()

    updated = (root / "index.html").read_text()
    for d in DISCIPLINES:
        assert f'slug:"{d.slug}"' in updated
    assert "artists: [],\n    // END GENERATED artists" in updated


def test_documented_script_invocation_does_not_fail_on_import():
    """tools/profiles.py's own docstring (and the README) document
    `python3 tools/profiles.py` as the way to run this generator. Absolute
    `tools.*` imports only resolve automatically under `python3 -m
    tools.profiles`, where `-m` puts the repo root on sys.path; run as a
    plain script, `tools` was not otherwise importable and every import
    below the first raised ModuleNotFoundError before main() ever ran.
    Proven here with a real subprocess — a sys.path bootstrap that looks
    right by inspection is not the same as the documented command actually
    working."""
    result = subprocess.run(  # noqa: S603 -- fixed argv, no untrusted input
        [sys.executable, "tools/profiles.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert "Traceback" not in result.stderr


# --- HTML-script-context escaping ------------------------------------------
#
# json.dumps() alone is not enough for a value embedded in an inline <script>
# element: it escapes quotes and backslashes correctly for the JS
# string-literal context, but does nothing about "<", ">", or "&" -- a value
# containing the literal substring "</script>" closes the surrounding
# <script> tag at the HTML-parser level regardless of how it's escaped as a
# JS string. Member names are transcribed by hand from what applicants type,
# so this is a reachable vector. Every test below proves both halves of the
# fix at once: the raw "<"/">"/"&" characters never reach the output, and a
# round-trip through json.loads() on the extracted literal recovers the
# exact original value -- which is only possible if the escaped form is
# still valid JS/JSON string syntax.


def _json_literal_for(body: str, key: str) -> str:
    """Pull the `key:"...js string..."` literal for `key` out of a rendered
    config block body and decode it with json.loads(). \\uXXXX escapes are
    valid JSON, so this both proves the emitted text still parses as a
    string literal and recovers the exact runtime value it decodes to."""
    match = re.search(rf'{key}:("(?:[^"\\]|\\.)*")', body)
    assert match, f'no {key}:"..." literal found in {body!r}'
    return json.loads(match.group(1))


def test_js_string_escapes_script_breakout_characters():
    raw = "Evil</script><img src=x onerror=alert(1)>&amp;"
    encoded = _js_string(raw)
    assert "<" not in encoded
    assert ">" not in encoded
    assert "&" not in encoded
    assert json.loads(encoded) == raw


def test_artist_name_containing_script_tag_is_escaped_in_generated_block(workspace):
    profiles, src, root = workspace
    (root / "index.html").write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles, name="Evil</script><img src=x onerror=alert(1)>&amp;")
    assert generate(profiles, src, root, TODAY) == 0

    updated = (root / "index.html").read_text()
    assert (
        "</script>"
        not in updated.split("// GENERATED artists", 1)[1].split(
            "// END GENERATED artists", 1
        )[0]
    )
    assert (
        _json_literal_for(updated, "name")
        == "Evil</script><img src=x onerror=alert(1)>&amp;"
    )


def test_js_string_escapes_the_forward_slash():
    """Every "/" comes out as the JS/JSON escape "\\/", so no member-supplied
    value can ever spell out a literal "//" — and therefore never this
    generator's own "// GENERATED" / "// END GENERATED" markers. "\\/" decodes
    back to "/", so the runtime value is unchanged."""
    encoded = _js_string("// END GENERATED artists")
    assert "//" not in encoded
    assert "// END GENERATED artists" not in encoded
    assert json.loads(encoded) == "// END GENERATED artists"
    assert json.loads(_js_string("a/b")) == "a/b"


MARKER_NAME = "Poison // END GENERATED artists"


def test_a_name_carrying_the_end_marker_cannot_poison_the_next_build(workspace):
    """Unescaped, a member name containing the literal end marker landed in
    index.html as that text. On the *next* build rewrite_block's partition
    matched it instead of the real marker and rewrote around the wrong split
    point, leaving a bare `],` at object-literal position — `var PCA` stopped
    parsing and the whole page's JS died. Both runs exited 0, so two builds
    are what this needs to prove."""
    profiles, src, root = workspace
    index = root / "index.html"
    index.write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles, name=MARKER_NAME)

    assert generate(profiles, src, root, TODAY) == 0
    assert generate(profiles, src, root, TODAY) == 0

    updated = index.read_text()
    assert updated.count("// END GENERATED artists") == 1
    assert _json_literal_for(updated, "name") == MARKER_NAME


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_generated_config_still_parses_as_javascript(workspace):
    profiles, src, root = workspace
    index = root / "index.html"
    index.write_text(HTML.replace("artists: []", ARTISTS_BLOCK))
    write_profile(profiles, name=MARKER_NAME)
    generate(profiles, src, root, TODAY)
    generate(profiles, src, root, TODAY)

    script = root / "config.js"
    script.write_text(index.read_text())
    result = subprocess.run(  # noqa: S603 -- fixed argv, generated input only
        [shutil.which("node"), "--check", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_discipline_label_containing_script_tag_is_escaped(monkeypatch):
    import tools.profiles as profiles_module

    evil = Discipline(
        slug="evil", label="Evil</script><img onerror=alert(1)>", hue="vermilion", t=1
    )
    monkeypatch.setattr(profiles_module, "DISCIPLINES", [evil])
    blocks = render_config_blocks([])
    assert "</script>" not in blocks["disciplines"]
    assert _json_literal_for(blocks["disciplines"], "label") == evil.label


def test_artist_url_containing_script_tag_is_escaped():
    """The url field is built from p.slug, which tools/profile_schema.py's
    slug regex already keeps free of special characters -- but
    render_config_blocks escapes it anyway (see its docstring), so this
    constructs a Profile directly (bypassing that upstream validation) to
    prove the escaping holds on its own, not only because callers happen to
    behave."""
    p = Profile(
        slug="x</script><img onerror=alert(1)>",
        name="Direct",
        disciplines=("painters",),
        tagline="t",
        bio=(),
        location="",
        portrait=None,
        works=(),
        links=(),
        availability=Availability(status="open", openTo=(), updated="2026-07-26"),
        consent={},
        joined="",
        live=True,
    )
    blocks = render_config_blocks([p])
    assert "</script>" not in blocks["artists"]
    assert _json_literal_for(blocks["artists"], "url") == f"/artists/{p.slug}/"


# --- disciplineLabels() (index.html client-side JS) ------------------------

DISCIPLINE_LABELS_JS = """
  function disciplineLabels(slugs) {
    var out = [];
    (slugs || []).forEach(function (slug) {
      for (var i = 0; i < PCA.disciplines.length; i++) {
        if (PCA.disciplines[i].slug === slug) { out.push(PCA.disciplines[i].label); break; }
      }
    });
    return out.join(', ');
  }"""


def test_discipline_labels_js_is_byte_identical_in_index_html():
    """Guards against the Node-executed copy below drifting from what
    index.html actually ships -- if someone edits the real function without
    updating this fixture, this fails loudly instead of the test below
    quietly validating stale logic."""
    html = (ROOT / "index.html").read_text()
    assert DISCIPLINE_LABELS_JS in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_discipline_labels_joins_two_and_skips_an_unknown_slug():
    script = (
        "global.PCA = { disciplines: ["
        "{ slug: 'painters', label: 'Painters' },"
        "{ slug: 'sculptors', label: 'Sculptors' }"
        "] };" + DISCIPLINE_LABELS_JS + "\nconsole.log(JSON.stringify({"
        "two: disciplineLabels(['painters', 'sculptors']),"
        "unknown: disciplineLabels(['does-not-exist']),"
        "}));"
    )
    result = subprocess.run(  # noqa: S603 -- fixed script, no untrusted input
        [shutil.which("node"), "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(result.stdout)
    assert out["two"] == "Painters, Sculptors"
    assert out["unknown"] == ""
    assert "undefined" not in out["unknown"]


# --- preview (draft review copies) -----------------------------------------


def test_preview_renders_a_not_live_draft_outside_the_tree(tmp_path):
    profiles = tmp_path / "drafts"
    profiles.mkdir()
    data = {k: v for k, v in VALID.items() if k not in ("consent", "portrait", "works")}
    data["live"] = False
    (profiles / "example-artist.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    assert preview(profiles, tmp_path / "intake", out, date(2026, 7, 26)) == 0
    page = (out / "artists" / "example-artist" / "index.html").read_text()
    assert "Draft — for the artist’s review; not published" in page
    assert (out / "artists" / "profile.css").is_file()
    assert (out / "img" / "artists" / "example-artist" / "tile.webp").is_file()


def test_preview_refuses_to_write_inside_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    profiles = tmp_path / "drafts"
    profiles.mkdir()
    data = {**VALID, "live": False, "portrait": None, "works": []}
    (profiles / "example-artist.json").write_text(json.dumps(data))
    out = repo / "site" / "preview"
    assert preview(profiles, tmp_path / "intake", out, date(2026, 7, 26)) == 2
    assert not out.exists()


def test_the_real_repo_is_a_refused_preview_target():
    assert preview(ROOT / "profiles", ROOT / "intake", ROOT / "preview", date(2026, 7, 26)) == 2
    assert not (ROOT / "preview").exists()
