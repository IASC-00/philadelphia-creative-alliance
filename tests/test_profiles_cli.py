# ruff: noqa: S101
import json
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
    render_config_blocks,
    rewrite_block,
)

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
    assert '"example-artist"' in updated
    assert "/artists/example-artist/" in updated


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
