#!/usr/bin/env python3
"""Generate artist profile pages from profiles/*.json.

Run from the repo root:
    python3 tools/profiles.py            # build
    python3 tools/profiles.py --check    # verify committed output is current

This is the artist-profiles counterpart to tools/mosaic.py, and follows the
same --check pattern for the same hard-won reason (see that module's
docstring): existence and "not a flat frame" were not enough on their own,
because an audit swapped in a retired asset and a 64x64 crop and an earlier
version of the check passed both. So --check here rebuilds every live
profile's page and images (into an isolated scratch directory, never
touching the real output — see check()'s own docstring for why) and compares
sha256 against the manifest committed alongside the output, rather than
trusting that a rebuild happened.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # `python3 tools/profiles.py` (the invocation this module's own docstring
    # and the README document) runs this file as a top-level script, so
    # `tools` is not yet on sys.path as an importable package the way
    # `python3 -m tools.profiles` puts it there automatically — the absolute
    # `tools.*` imports below would otherwise raise ModuleNotFoundError.
    # Inserting the repo root before those imports run makes both
    # invocations work identically.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import date

from tools.mosaic import ROOT, sha256_of
from tools.profile_images import process_profile_images
from tools.profile_render import render_profile
from tools.profile_schema import DISCIPLINES, ProfileError, load_all

PROFILES_DIR = ROOT / "profiles"
INTAKE_DIR = ROOT / "intake"
ARTIST_MANIFEST = Path("img/artists/manifest.json")


def rewrite_block(html: str, marker: str, body: str) -> str:
    """Replace the text between a `// GENERATED <marker>` /
    `// END GENERATED <marker>` marker pair with `body`.

    Idempotent: re-running with the same body reproduces the exact same
    output, because the region between the markers (including whatever
    trailing comment text followed the opening marker on its first run) is
    fully replaced each time, never appended to or diffed against.

    Missing markers are an error, never a silent no-op — a hand-edited or
    misconfigured index.html must fail loudly rather than have this pass
    quietly do nothing while reporting success.
    """
    start = f"// GENERATED {marker}"
    end = f"// END GENERATED {marker}"
    head, sep, rest = html.partition(start)
    if not sep:
        raise ValueError(f"marker not found: {start}")
    _stale, sep2, tail = rest.partition(end)
    if not sep2:
        raise ValueError(f"marker not found: {end}")
    opener = " — tools/profiles.py rewrites this block, do not hand-edit\n"
    return f"{head}{start}{opener}{body}\n    {end}{tail}"


def render_config_blocks(profiles) -> dict[str, str]:
    """Render the `disciplines` and `artists` array literals for the `PCA`
    config block in index.html, from the taxonomy and the live profile list.

    String values are `json.dumps`-encoded rather than hand-quoted: artist
    names and labels are member-supplied text, so a name containing a quote
    or a backslash must not be able to break out of the JS string literal.
    """
    disciplines = ",\n".join(
        f"      {{ slug:'{d.slug}', label:{json.dumps(d.label)}, t:{d.t} }}"
        for d in DISCIPLINES
    )
    artist_entries = [
        f"      {{ slug:'{p.slug}', name:{json.dumps(p.name)}, "
        f"disciplines:{json.dumps(list(p.disciplines))}, "
        f"url:'/artists/{p.slug}/' }}"
        for p in profiles
    ]
    # A genuinely empty roster (DISCIPLINES is never empty, but zero live
    # artists is a normal, legitimate state — see generate()) renders as the
    # compact `artists: [],` rather than an empty-looking multi-line array.
    artists_body = (
        "    artists: [\n" + ",\n".join(artist_entries) + "\n    ],"
        if artist_entries
        else "    artists: [],"
    )
    return {
        "disciplines": f"    disciplines: [\n{disciplines}\n    ],",
        "artists": artists_body,
    }


def _build(profiles_dir: Path, src_dir: Path, root: Path, today: date):
    """Load every profile and render pages and images for the live ones.

    Shared by generate() and check(): check() rebuilds instead of trusting
    that generate() was run, since a matching manifest is what proves the
    committed bytes are the ones this generator currently produces.

    Returns `(all_profiles, live, pages, manifest)`. `all_profiles` (before
    the `live` filter) is what generate() uses to tell "no profile files were
    found at all" — almost certainly a misconfigured path — apart from "every
    profile that exists happens to be marked not live," which is a legitimate
    editorial choice and must still publish (i.e. prune) correctly.
    """
    all_profiles = load_all(profiles_dir)
    live = [p for p in all_profiles if p.live]
    pages: dict[str, str] = {}
    manifest: dict[str, dict] = {}
    for profile in live:
        manifest.update(
            process_profile_images(profile, src_dir, Path(root) / "img" / "artists")
        )
        pages[profile.slug] = render_profile(profile, today)
    return all_profiles, live, pages, manifest


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _previously_known_slugs(manifest_path: Path) -> set[str]:
    """Slugs this generator has published before, per the last committed
    manifest. Only these are ever eligible for removal — a directory under
    artists/ that was never in the manifest was not created by this tool and
    is not ours to delete, no matter what it's named.

    A missing or unreadable manifest yields an empty set, which is the safe
    direction: nothing gets pruned rather than guessing.
    """
    if not manifest_path.exists():
        return set()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    return {
        key.rsplit("/", 1)[0]
        for key in manifest
        if isinstance(key, str) and key.endswith("/index.html")
    }


def _remove_stale_profile_dirs(
    root: Path, live_slugs: set[str], known_slugs: set[str]
) -> None:
    """Prune artists/<slug>/ and img/artists/<slug>/ for slugs this tool has
    previously published (`known_slugs`) that are no longer live — removed
    entirely, or flipped to `live: false`.

    Three guards, each closing a distinct way this pass could destroy
    something it shouldn't:
      - `known_slugs` scoping: a directory that was never in a previously
        committed manifest (artists/profile.css's directory, a hand-made
        artists/_shared/, ...) is never touched, regardless of its name.
      - `is_symlink()`: checked before `is_dir()`, because `Path.is_dir()`
        follows symlinks and would otherwise let a symlinked directory
        through the guard straight into `shutil.rmtree`, which raises on a
        symlink rather than silently doing something safe.
      - `is_dir()`: only directories are ever removed, never plain files.
    """
    artists_dir = root / "artists"
    if not artists_dir.exists():
        return
    for existing in sorted(artists_dir.iterdir()):
        if existing.is_symlink():
            continue
        if not existing.is_dir():
            continue
        if existing.name not in known_slugs:
            continue
        if existing.name in live_slugs:
            continue
        shutil.rmtree(existing)
        images = root / "img" / "artists" / existing.name
        if images.exists() and not images.is_symlink():
            shutil.rmtree(images)
        print(f"  removed artists/{existing.name}")


def generate(profiles_dir: Path, src_dir: Path, root: Path, today: date) -> int:
    """Build every live profile's page and images, prune stale ones, and
    regenerate the taxonomy/roster config blocks in index.html.

    Builds entirely into an isolated scratch directory first, and only moves
    anything into `root` once every live profile has succeeded — a missing
    intake image for the *second* of two profiles must not leave the first
    profile's images sitting in `root` with no page or manifest entry for
    them, which is what writing directly into `root` during the build loop
    would do.

    Zero profile files found (a missing or empty profiles/ directory) skips
    only the *removal* pass — the one step capable of deleting a previously
    published page — and is reported as a warning, not a fatal error. It
    used to abort the entire run, but that conflated two unrelated concerns:
    pruning published pages needs the guard (a mistyped path must never read
    as "unpublish everyone"), while regenerating index.html's discipline
    taxonomy does not — that block is a function of DISCIPLINES in
    tools/profile_schema.py alone, not of whether any artist has joined, so
    a fresh repo with zero members must still regenerate it and succeed.
    Distinct from every profile being live:false (a real editorial choice
    captured in real files), which still publishes and prunes normally —
    see test_live_false_removes_a_previously_built_page.
    """
    root = Path(root)
    with tempfile.TemporaryDirectory() as scratch_str:
        scratch = Path(scratch_str)
        try:
            all_profiles, live, pages, manifest = _build(
                profiles_dir, src_dir, scratch, today
            )
        except (ProfileError, FileNotFoundError) as exc:
            print(f"FAILED: {exc}")
            return 1

        slugs = {p.slug for p in live}
        if all_profiles:
            known = _previously_known_slugs(root / ARTIST_MANIFEST)
            _remove_stale_profile_dirs(root, slugs, known)
        else:
            print(
                f"WARNING: no profile files found in {profiles_dir} — skipping "
                "the prune pass so a mistyped path can never delete a "
                "previously published page. If this is a fresh repo with no "
                "members yet, that's expected; index.html's config blocks are "
                "still regenerated below."
            )

        artists_dir = root / "artists"
        artists_dir.mkdir(parents=True, exist_ok=True)
        images_root = root / "img" / "artists"
        images_root.mkdir(parents=True, exist_ok=True)
        scratch_images_root = scratch / "img" / "artists"

        for slug in slugs:
            dest = images_root / slug
            if dest.exists() and not dest.is_symlink():
                shutil.rmtree(dest)
            shutil.move(str(scratch_images_root / slug), str(dest))

        for slug, html in pages.items():
            target = artists_dir / slug / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            manifest[f"{slug}/index.html"] = {"sha256": _sha256_of_text(html)}
            print(f"  artists/{slug}/index.html")

        manifest_path = root / ARTIST_MANIFEST
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\n  {len(pages)} profiles, {len(manifest)} manifest entries")

        index = root / "index.html"
        if index.exists():
            html = index.read_text()
            for marker, body in render_config_blocks(live).items():
                html = rewrite_block(html, marker, body)
            index.write_text(html)
            print("  index.html config blocks updated")

        return 0


def check(profiles_dir: Path, src_dir: Path, root: Path, today: date) -> int:
    """Verify the committed pages and images are what this generator produces.

    Rebuilding directly into `root` (as a naive implementation of this check
    might) would overwrite a tampered or stale file with the correct bytes
    *before* comparing it — silently healing the very drift this check exists
    to catch. So the rebuild happens in an isolated scratch directory instead,
    leaving `root` completely untouched, and two comparisons run against it:

    1. fresh rebuild vs. the manifest committed alongside the output — a
       mismatch means a profile or intake image changed and the generator
       was not re-run (staleness).
    2. the on-disk output vs. that same committed manifest — a mismatch
       means an output file was hand-edited or swapped after generation
       (tampering), independent of whether the source has since changed.
    """
    root = Path(root)
    manifest_path = root / ARTIST_MANIFEST
    if not manifest_path.exists():
        print(f"FAILED: {ARTIST_MANIFEST} missing — run without --check first")
        return 1

    with tempfile.TemporaryDirectory() as scratch:
        try:
            _all, _live, pages, fresh_manifest = _build(
                profiles_dir, src_dir, Path(scratch), today
            )
        except (ProfileError, FileNotFoundError) as exc:
            print(f"FAILED: {exc}")
            return 1

    for slug, html in pages.items():
        fresh_manifest[f"{slug}/index.html"] = {"sha256": _sha256_of_text(html)}

    try:
        committed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(committed, dict):
            raise TypeError(f"expected an object, got {type(committed).__name__}")

        failures = []

        for key in sorted(set(fresh_manifest) | set(committed)):
            if key not in committed:
                failures.append(f"{key}: missing from the committed manifest (stale)")
            elif key not in fresh_manifest:
                failures.append(
                    f"{key}: in the committed manifest but no longer produced"
                )
            elif fresh_manifest[key]["sha256"] != committed[key]["sha256"]:
                failures.append(
                    f"{key}: manifest does not match a fresh rebuild (stale)"
                )

        for key, entry in committed.items():
            if key.endswith("index.html"):
                slug = key.rsplit("/", 1)[0]
                target = root / "artists" / slug / "index.html"
            else:
                target = root / "img" / "artists" / key
            if not target.exists():
                failures.append(f"{key}: missing")
            elif sha256_of(target) != entry["sha256"]:
                failures.append(f"{key}: does not match manifest (stale or replaced)")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"FAILED: manifest is unreadable ({exc})")
        return 1

    if failures:
        print("\nFAILED:")
        for f in sorted(set(failures)):
            print(f"  {f}")
        return 1
    print(f"All {len(committed)} profile artefacts match the manifest.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify outputs only")
    args = ap.parse_args()
    runner = check if args.check else generate
    sys.exit(runner(PROFILES_DIR, INTAKE_DIR, ROOT, date.today()))
