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

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from tools.mosaic import ROOT, sha256_of
from tools.profile_images import process_profile_images
from tools.profile_render import render_profile
from tools.profile_schema import ProfileError, load_all

PROFILES_DIR = ROOT / "profiles"
INTAKE_DIR = ROOT / "intake"
ARTIST_MANIFEST = Path("img/artists/manifest.json")


def _build(profiles_dir: Path, src_dir: Path, root: Path, today: date):
    """Load every live profile and render its page and images.

    Shared by generate() and check(): check() rebuilds instead of trusting
    that generate() was run, since a matching manifest is what proves the
    committed bytes are the ones this generator currently produces.
    """
    live = [p for p in load_all(profiles_dir) if p.live]
    pages: dict[str, str] = {}
    manifest: dict[str, dict] = {}
    for profile in live:
        manifest.update(
            process_profile_images(profile, src_dir, Path(root) / "img" / "artists")
        )
        pages[profile.slug] = render_profile(profile, today)
    return live, pages, manifest


def _remove_stale_profile_dirs(root: Path, live_slugs: set[str]) -> None:
    """Prune artists/<slug>/ and img/artists/<slug>/ for profiles that are no
    longer live (removed, or flipped to live: false).

    Only ever removes directories, never files. artists/profile.css — the
    shared stylesheet — lives in this same artists/ directory, so an
    `is_dir()` guard is what keeps this pass from ever reaching it.
    """
    artists_dir = root / "artists"
    if not artists_dir.exists():
        return
    for existing in artists_dir.iterdir():
        if existing.is_dir() and existing.name not in live_slugs:
            shutil.rmtree(existing)
            images = root / "img" / "artists" / existing.name
            if images.exists():
                shutil.rmtree(images)
            print(f"  removed artists/{existing.name}")


def generate(profiles_dir: Path, src_dir: Path, root: Path, today: date) -> int:
    root = Path(root)
    try:
        live, pages, manifest = _build(profiles_dir, src_dir, root, today)
    except (ProfileError, FileNotFoundError) as exc:
        print(f"FAILED: {exc}")
        return 1

    artists_dir = root / "artists"
    artists_dir.mkdir(parents=True, exist_ok=True)
    slugs = {p.slug for p in live}

    _remove_stale_profile_dirs(root, slugs)

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
    return 0


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            _live, pages, fresh_manifest = _build(
                profiles_dir, src_dir, Path(scratch), today
            )
        except (ProfileError, FileNotFoundError) as exc:
            print(f"FAILED: {exc}")
            return 1

    for slug, html in pages.items():
        fresh_manifest[f"{slug}/index.html"] = {"sha256": _sha256_of_text(html)}

    committed = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []

    for key in sorted(set(fresh_manifest) | set(committed)):
        if key not in committed:
            failures.append(f"{key}: missing from the committed manifest (stale)")
        elif key not in fresh_manifest:
            failures.append(f"{key}: in the committed manifest but no longer produced")
        elif fresh_manifest[key]["sha256"] != committed[key]["sha256"]:
            failures.append(f"{key}: manifest does not match a fresh rebuild (stale)")

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
