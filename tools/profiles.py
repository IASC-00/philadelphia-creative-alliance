#!/usr/bin/env python3
"""Generate artist profile pages from profiles/*.json.

Run from the repo root:
    python3 tools/profiles.py            # build
    python3 tools/profiles.py --check    # verify committed output is current

This is the artist-profiles counterpart to tools/mosaic.py and follows the
same --check pattern for the same reason that module gives: it compares
committed output against a committed manifest *rather than* trusting a
rebuild to match, because encoder output moves with Pillow and libwebp.

What each half of --check can do differs by what it needs on disk:

  - Page HTML is a function of the profile JSON alone, and profiles/ is
    committed. Pages are therefore always re-rendered and compared in full —
    on a fresh clone and in CI exactly as on the authoring machine.
  - Images are a function of the submitted originals in intake/, which is
    deliberately gitignored (only the processed WebP is published). When
    those originals are present the images are rebuilt and compared too;
    when they are absent the run says so explicitly and falls back to
    comparing the committed manifest against the files on disk, rather than
    failing for a reason nobody away from the authoring machine can fix.

Both halves rebuild into an isolated scratch directory and never touch the
real output tree — see check()'s own docstring for why that matters.
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
from tools.profile_schema import (
    DISCIPLINES,
    ProfileError,
    STALE_AFTER_DAYS,
    availability_is_current,
    load_all,
)

PROFILES_DIR = ROOT / "profiles"
INTAKE_DIR = ROOT / "intake"
ARTIST_MANIFEST = Path("img/artists/manifest.json")

# Manifest keys starting with "_" are metadata about the build, not artefacts
# to compare. Slugs can never collide with them: tools/profile_schema.py's slug
# regex only admits lowercase letters, digits and single hyphens.
META_KEY = "_meta"


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


def _js_string(value: str) -> str:
    """Encode `value` as a JS string literal that is also safe to embed
    inside an inline <script> element, not merely inside a JS expression.

    `json.dumps` already handles quotes and backslashes correctly for the
    JS string-literal context, but it does nothing about `<`, `>`, `&`, or
    `/`.

    A value containing the literal substring `</script>` would close the
    surrounding <script> tag at the HTML-parser level — regardless of how
    correctly it's escaped as a JS string — and let arbitrary markup run
    right after it. Member names (and, in principle, anything else that
    reaches this function) are transcribed by hand from what applicants
    type, so this is a reachable vector, not a theoretical one.

    `/` is the same class of problem one layer down, against this
    generator's own markers rather than the HTML parser. A name containing
    `// END GENERATED artists` lands in index.html as exactly that text, and
    on the *next* run rewrite_block's `partition` matches it instead of the
    real marker and rewrites around the wrong split point. The tail is then
    swallowed by the `//` comment, leaving a bare `],` at object-literal
    position: `var PCA` stops parsing, and with it the org wall, the
    discipline grid, the roster, both join forms and the donate button.
    Both runs exit 0.

    Re-encoding all four keeps the result valid JS — `\\uXXXX` and `\\/` are
    both legal inside a JS (and JSON) string literal, so this can't break
    parsing — and leaves the decoded runtime value exactly unchanged, while
    making it impossible for the HTML parser, or a later pass of this
    generator, to see another `<`, `>`, `&`, or `/` in the output.
    """
    encoded = json.dumps(value)
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("/", "\\/")
    )


def render_config_blocks(profiles) -> dict[str, str]:
    """Render the `disciplines` and `artists` array literals for the `PCA`
    config block in index.html, from the taxonomy and the live profile list.

    Every interpolated value — slugs and URLs included, not only names and
    labels — goes through `_js_string()` rather than hand-quoting. Slugs are
    validated elsewhere (DISCIPLINES is a fixed constant; profile slugs are
    constrained by tools/profile_schema.py's slug regex), but applying the
    same escaping uniformly means this function stays safe on its own terms
    even if that upstream validation ever changes, rather than depending on
    every caller to keep it that way.
    """
    disciplines = ",\n".join(
        f"      {{ slug:{_js_string(d.slug)}, label:{_js_string(d.label)}, t:{d.t} }}"
        for d in DISCIPLINES
    )
    artist_entries = [
        f"      {{ slug:{_js_string(p.slug)}, name:{_js_string(p.name)}, "
        f"disciplines:[{', '.join(_js_string(s) for s in p.disciplines)}], "
        f"url:{_js_string(f'/artists/{p.slug}/')} }}"
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


def _load_live(profiles_dir: Path):
    """Return `(all_profiles, live)`.

    `all_profiles` (before the `live` filter) is what generate() uses to tell
    "no profile files were found at all" — almost certainly a misconfigured
    path — apart from "every profile that exists happens to be marked not
    live," which is a legitimate editorial choice and must still publish
    (i.e. prune) correctly.
    """
    all_profiles = load_all(profiles_dir)
    return all_profiles, [p for p in all_profiles if p.live]


def _render_pages(live, today: date) -> dict[str, str]:
    """Render each live profile's page. Depends on the profile JSON alone —
    no image, and nothing outside the committed tree, reaches this — which is
    why check() can always run it, even where intake/ is absent."""
    return {profile.slug: render_profile(profile, today) for profile in live}


def _process_images(live, src_dir: Path, out_root: Path) -> dict[str, dict]:
    manifest: dict[str, dict] = {}
    for profile in live:
        manifest.update(process_profile_images(profile, src_dir, Path(out_root)))
    return manifest


def _missing_originals(live, src_dir: Path) -> dict[str, list[str]]:
    """Submitted originals referenced by a live profile that are not on disk,
    grouped by slug. Only profiles with at least one missing file appear.

    intake/ is gitignored on purpose, so on a fresh clone or a CI runner every
    profile is in here and an image rebuild is simply not available — a fact
    check() reports rather than failing over.

    Grouped rather than flat because the decision is per profile. Treating it
    as one global yes/no meant a single absent original disabled image
    verification for *every* artist — so a genuinely stale image on a profile
    whose own originals were all present sailed through with an exit 0 and a
    NOTE reassuring the operator it was expected. That fires on the authoring
    machine, where the check is fully capable for every other profile.
    """
    src_dir = Path(src_dir)
    missing: dict[str, list[str]] = {}
    for profile in live:
        sources = list(profile.works)
        if profile.portrait:
            sources.append(profile.portrait)
        absent = sorted(
            item.src for item in sources if not (src_dir / item.src).exists()
        )
        if absent:
            missing[profile.slug] = absent
    return missing


def _build(profiles_dir: Path, src_dir: Path, root: Path, today: date):
    """Load every profile and render pages and images for the live ones.

    Returns `(all_profiles, live, pages, manifest)`.
    """
    all_profiles, live = _load_live(profiles_dir)
    manifest = _process_images(live, src_dir, Path(root) / "img" / "artists")
    pages = _render_pages(live, today)
    return all_profiles, live, pages, manifest


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_manifest(manifest_path: Path) -> dict:
    """The manifest currently on disk, or `{}` if it is missing or unreadable.

    Never raises: every caller here wants the safe direction (treat it as
    empty) rather than a crash, and check() reads and validates it separately
    so an unreadable manifest still fails the guard loudly.
    """
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _artefacts(manifest: dict) -> dict:
    """Only the file entries — `_meta` records when the build ran, not a file
    to compare, and must never reach the artefact comparisons."""
    return {
        key: value
        for key, value in manifest.items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _generated_on(manifest: dict) -> date | None:
    """The date the committed artefacts were rendered, per `_meta`.

    check() re-renders using this rather than today's date. Without it,
    crossing the 90-day availability window turns --check red with nothing on
    disk having changed: the badge stops rendering on day 91, so a fresh
    rebuild diverges from committed output for a reason no human caused.
    A missing or malformed value yields None and the caller falls back to
    today — the pre-`_meta` behaviour, not a hard failure.
    """
    meta = manifest.get(META_KEY)
    if not isinstance(meta, dict):
        return None
    try:
        return date.fromisoformat(meta.get("generated", ""))
    except (ValueError, TypeError):
        return None


def _previously_known_slugs(manifest: dict) -> set[str]:
    """Slugs this generator has published before, per the last committed
    manifest. Only these are ever eligible for removal — a directory under
    artists/ that was never in the manifest was not created by this tool and
    is not ours to delete, no matter what it's named.

    A missing or unreadable manifest yields an empty set, which is the safe
    direction: nothing gets pruned rather than guessing.
    """
    return {
        key.rsplit("/", 1)[0]
        for key in _artefacts(manifest)
        if key.endswith("/index.html")
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
        # Also any staging directory a previously interrupted swap left behind
        # for this slug: it holds the same member's artwork and is served just
        # as publicly as the real directory.
        _remove_path(_staging_path(images))
        print(f"  removed artists/{existing.name}")


def _swap_into_place(built: Path, dest: Path) -> None:
    """Publish `built` (a freshly built directory in the scratch tree) at
    `dest`, without ever deleting `dest` before its replacement is already on
    the destination filesystem.

    Deleting first and moving second — which is what this used to do — has two
    distinct failure modes, both of which destroy an already-published
    artist's images and put nothing back: a cross-device `shutil.move` that
    fails partway (a full disk, a permissions change) after the old directory
    is already gone, and a Ctrl-C landing in the window between the two calls.
    The scratch tree is a TemporaryDirectory, typically on a different
    filesystem, so that window is a real copy, not an instant rename.

    Staging first closes both: the copy lands on a sibling hidden path under
    the destination's own parent, so if anything fails during it the live
    directory is still untouched. Only once the replacement is fully present
    on the destination filesystem is the old one removed, and the final step
    is a same-filesystem rename.

    A failed copy is cleaned up before the exception propagates. A partial
    `.<slug>.incoming` left under img/artists/ is *published* — img/ is not
    gitignored — so a member taken down later would keep their artwork served
    from the staging path indefinitely, with --check green, because neither
    the prune nor the orphan guard used to look at it. For a project whose
    whole premise is that only consented work is published, that is the
    failure that matters most.
    """
    staging = _staging_path(dest)
    _remove_path(staging)
    try:
        shutil.move(str(built), str(staging))
    except BaseException:
        _remove_path(staging)
        raise
    _remove_path(dest)
    staging.rename(dest)


def _staging_path(dest: Path) -> Path:
    """The sibling hidden path `_swap_into_place` copies into before swapping.

    A slug can never collide with this name: the slug regex admits only
    lowercase letters, digits and single hyphens, so nothing can start with
    a dot.
    """
    return dest.parent / f".{dest.name}.incoming"


def _remove_path(target: Path) -> None:
    """Remove `target` whatever it is, and do nothing if it isn't there.

    `is_symlink()` is checked before `is_dir()` because `Path.is_dir()`
    follows symlinks, and `shutil.rmtree` raises on a symlink rather than
    doing anything safe — the symlink itself is what has to go.
    """
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def generate(profiles_dir: Path, src_dir: Path, root: Path, today: date) -> int:
    """Build every live profile's page and images, prune stale ones, and
    regenerate the taxonomy/roster config blocks in index.html.

    Builds entirely into an isolated scratch directory first, and only moves
    anything into `root` once every live profile has succeeded — a missing
    intake image for the *second* of two profiles must not leave the first
    profile's images sitting in `root` with no page or manifest entry for
    them, which is what writing directly into `root` during the build loop
    would do.

    The prune runs *last*, after the pages, the manifest and index.html have
    all been written. It used to run first, which meant any failure after it
    (a hand-edited index.html missing a marker, which rewrite_block raises on
    by design) left the live site linking to pages that had already been
    deleted. Pruning last inverts the failure: the worst case is now a
    directory that outlives its manifest entry, which --check reports as an
    orphan rather than a visitor hitting a 404.

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

    When that guard fires, the previously published manifest entries are
    carried forward rather than overwritten with the (smaller, here empty)
    set this run produced. The manifest is the only record of what has been
    published, and _previously_known_slugs reads it to decide what may ever
    be pruned — so emptying it while declining to prune stranded the pages
    permanently and made the documented `live: false` takedown stop working
    too, all while exiting 0.
    """
    root = Path(root)
    manifest_path = root / ARTIST_MANIFEST
    committed = _read_manifest(manifest_path)

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
        prune = bool(all_profiles)
        known = _previously_known_slugs(committed) if prune else set()

        # Resolve index.html completely before anything is written. A missing
        # marker is the one realistic raise in this function, and it used to
        # land between the manifest write and the prune — where it is not
        # merely a failed run but an unrecoverable one. The manifest written
        # moments earlier no longer lists the member being taken down, so the
        # *next* run's `known` no longer contains them either and the prune
        # skips them on every future run: their page and photos stay published
        # forever, exit 0. Failing here instead leaves the tree exactly as it
        # was, so re-running after fixing index.html completes the takedown.
        index = root / "index.html"
        rewritten_index = None
        if index.exists():
            html = index.read_text(encoding="utf-8")
            for marker, body in render_config_blocks(live).items():
                html = rewrite_block(html, marker, body)
            rewritten_index = html

        if not prune:
            manifest = {**_artefacts(committed), **manifest}
            print(
                f"WARNING: no profile files found in {profiles_dir} — skipping "
                "the prune pass so a mistyped path can never delete a "
                "previously published page, and carrying the previously "
                "published manifest entries forward unchanged.\n"
                '  To take a member down, set "live": false in their '
                "profiles/<slug>.json and re-run this generator: that is what "
                "removes their page and images. Deleting the record instead "
                "leaves the published page and photos in place, and --check "
                "will report the page as an orphan until the record is "
                "restored or the page is removed by hand.\n"
                "  If this is a fresh repo with no members yet, nothing has "
                "been published and there is nothing to carry forward; "
                "index.html's config blocks are still regenerated below."
            )

        artists_dir = root / "artists"
        artists_dir.mkdir(parents=True, exist_ok=True)
        images_root = root / "img" / "artists"
        images_root.mkdir(parents=True, exist_ok=True)
        scratch_images_root = scratch / "img" / "artists"

        for slug in sorted(slugs):
            _swap_into_place(scratch_images_root / slug, images_root / slug)

        for slug, html in sorted(pages.items()):
            target = artists_dir / slug / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            manifest[f"{slug}/index.html"] = {"sha256": _sha256_of_text(html)}
            print(f"  artists/{slug}/index.html")

        if rewritten_index is not None:
            index.write_text(rewritten_index, encoding="utf-8")
            print("  index.html config blocks updated")

        if prune:
            _remove_stale_profile_dirs(root, slugs, known)

        # The manifest is written LAST, so it is the commit point for the whole
        # run. It is the record `known` is rebuilt from next time, so anything
        # that raises before this line must leave the *old* manifest intact —
        # otherwise a half-finished run silently narrows what the prune is ever
        # allowed to touch again. Dying between the prune and here is the safe
        # direction: the manifest still lists files that are gone, --check
        # reports them missing, and re-running fixes it.
        #
        # `_meta.generated` is what check() re-renders against, so it must
        # describe the bytes actually on disk. When this run reproduced the
        # committed artefacts exactly, the recorded date still reproduces
        # them and is kept — otherwise every build would rewrite the manifest
        # with today's date and dirty the tree for no change in output.
        generated = today
        if manifest == _artefacts(committed):
            generated = _generated_on(committed) or today
        written = {**manifest, META_KEY: {"generated": generated.isoformat()}}
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\n  {len(pages)} profiles, {len(manifest)} manifest entries")

        return 0


def _orphaned_page_failures(root: Path, committed: dict) -> list[str]:
    """Anything published that no manifest entry accounts for — a page under
    artists/, or an image directory under img/artists/.

    Without this, deleting a member's record — the natural response to
    "please remove me" — could pass green: the prune is skipped when no
    profile files remain, so the page and photos stay on disk while nothing
    in the manifest still refers to them.

    The page half is anchored on index.html rather than on the directory
    alone, so a hand-made artists/_shared/ (which the prune is likewise
    forbidden to touch) is not reported as an orphan.

    The image half exists because removing an orphaned *page* by hand — which
    is what this guard tells the operator to do — leaves the photos published
    at img/artists/<slug>/ and the gate green, so the de-listed member's work
    is still being served. It also catches `.<slug>.incoming` staging
    directories left by an interrupted swap, which are published the same way.
    """
    root = Path(root)
    failures = []

    artists_dir = root / "artists"
    if artists_dir.is_dir():
        for entry in sorted(artists_dir.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not (entry / "index.html").is_file():
                continue
            if f"{entry.name}/index.html" not in committed:
                failures.append(
                    f"artists/{entry.name}/index.html: published but absent from "
                    'the manifest — an orphaned page. Set "live": false on that '
                    "profile and re-run the generator to take the member down "
                    "properly; that removes the page and the images together."
                )

    images_root = root / "img" / "artists"
    if images_root.is_dir():
        accounted = {key.split("/", 1)[0] for key in committed if "/" in key}
        for entry in sorted(images_root.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name in accounted:
                continue
            failures.append(
                f"img/artists/{entry.name}/: published images absent from the "
                "manifest — an orphaned image directory. These are still being "
                'served. Set "live": false on that profile and re-run the '
                "generator, which removes them."
            )
    return failures


def _config_block_failures(root: Path, live) -> list[str]:
    """index.html's two generated blocks vs. what a fresh render produces.

    generate() rewrites these blocks, but nothing in the manifest covers
    them, so a dropped merge hunk or a partially committed roster used to
    pass clean — leaving an artist invisible on the wall, or linked to a 404,
    with CI green. rewrite_block is idempotent, so re-running it over the
    committed HTML is a no-op exactly when the committed block already
    matches; that also reuses the generator's own code path rather than
    reimplementing the marker parsing a second way.
    """
    index = Path(root) / "index.html"
    if not index.exists():
        return []
    html = index.read_text(encoding="utf-8")
    failures = []
    for marker, body in render_config_blocks(live).items():
        try:
            if rewrite_block(html, marker, body) != html:
                failures.append(
                    f"index.html: the generated '{marker}' block does not match "
                    "a fresh render (stale or hand-edited)"
                )
        except ValueError as exc:
            failures.append(f"index.html: {exc}")
    return failures


def check(profiles_dir: Path, src_dir: Path, root: Path, today: date) -> int:
    """Verify the committed pages and images are what this generator produces.

    Rebuilding directly into `root` (as a naive implementation of this check
    might) would overwrite a tampered or stale file with the correct bytes
    *before* comparing it — silently healing the very drift this check exists
    to catch. So the rebuild happens in an isolated scratch directory instead,
    leaving `root` completely untouched, and these comparisons run against it:

    1. freshly rendered pages vs. the manifest committed alongside the output
       — a mismatch means a profile changed and the generator was not re-run
       (staleness). Always available: pages come from committed JSON only.
    2. freshly rebuilt images vs. that same manifest — the same staleness
       check for imagery, but only when the submitted originals are on disk.
       intake/ is gitignored, so away from the authoring machine this half is
       reported as skipped rather than failed (see the module docstring).
    3. the on-disk output vs. the committed manifest — a mismatch means an
       output file was hand-edited or swapped after generation (tampering),
       independent of whether the source has since changed.
    4. index.html's generated config blocks vs. a fresh render.
    5. pages under artists/ that no manifest entry accounts for.

    The pages are re-rendered using the date recorded in the manifest, not
    today's, so that crossing the 90-day availability window cannot turn this
    red with nothing on disk having changed. Availability that has genuinely
    gone stale is reported as a notice instead — it needs a human to
    re-confirm and regenerate, which is not the same as a broken build.
    """
    root = Path(root)
    manifest_path = root / ARTIST_MANIFEST
    if not manifest_path.exists():
        print(f"FAILED: {ARTIST_MANIFEST} missing — run without --check first")
        return 1

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"expected an object, got {type(raw).__name__}")
    except (ValueError, TypeError) as exc:
        print(f"FAILED: manifest is unreadable ({exc})")
        return 1
    committed = _artefacts(raw)

    try:
        _all, live = _load_live(profiles_dir)
    except ProfileError as exc:
        print(f"FAILED: {exc}")
        return 1

    pages = _render_pages(live, _generated_on(raw) or today)
    fresh = {
        f"{slug}/index.html": {"sha256": _sha256_of_text(html)}
        for slug, html in pages.items()
    }

    # Per profile, not globally: a profile whose own originals are all present
    # gets its images rebuilt and fully compared even when another profile's
    # are missing.
    absent = _missing_originals(live, src_dir)
    buildable = [p for p in live if p.slug not in absent]
    if absent:
        example = f"{sorted(absent)[0]}/{absent[sorted(absent)[0]][0]}"
        print(
            f"NOTE: {sum(len(v) for v in absent.values())} submitted original(s) "
            f"for {len(absent)} of {len(live)} profile(s) are not in {src_dir} "
            f"(e.g. {example}) — intake/ is gitignored, so this is expected on a "
            "fresh clone or a CI runner. Images for those profiles "
            f"({', '.join(sorted(absent))}) are compared against the committed "
            "manifest only. Page HTML is rebuilt and compared in full for every "
            "profile, and images are rebuilt in full for the rest."
        )
    if buildable:
        with tempfile.TemporaryDirectory() as scratch:
            try:
                fresh.update(
                    _process_images(
                        buildable, src_dir, Path(scratch) / "img" / "artists"
                    )
                )
            except (ProfileError, FileNotFoundError) as exc:
                print(f"FAILED: {exc}")
                return 1

    live_slugs = {p.slug for p in live}
    rebuilt_slugs = {p.slug for p in buildable}
    try:
        failures = []

        page_keys = {k for k in set(fresh) | set(committed) if k.endswith("index.html")}
        image_keys = (set(fresh) | set(committed)) - page_keys

        for key in sorted(page_keys):
            if key not in committed:
                failures.append(f"{key}: missing from the committed manifest (stale)")
            elif key not in fresh:
                failures.append(
                    f"{key}: in the committed manifest but no longer produced"
                )
            elif fresh[key]["sha256"] != committed[key]["sha256"]:
                failures.append(
                    f"{key}: manifest does not match a fresh rebuild (stale)"
                )

        for key in sorted(image_keys):
            if key.split("/", 1)[0] not in rebuilt_slugs:
                # This profile's originals are absent, so there is no rebuild
                # to compare against; slug-level presence is still knowable
                # from the profiles, which are committed.
                if key.split("/", 1)[0] not in live_slugs:
                    failures.append(
                        f"{key}: in the committed manifest but no longer produced"
                    )
            elif key not in committed:
                failures.append(f"{key}: missing from the committed manifest (stale)")
            elif key not in fresh:
                failures.append(
                    f"{key}: in the committed manifest but no longer produced"
                )
            elif fresh[key]["sha256"] != committed[key]["sha256"]:
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

    failures.extend(_orphaned_page_failures(root, committed))
    failures.extend(_config_block_failures(root, live))

    for profile in live:
        if not availability_is_current(profile, today):
            print(
                f"NOTICE: {profile.slug}: availability was last confirmed more "
                f"than {STALE_AFTER_DAYS} days ago "
                f"({profile.availability.updated or 'never'}) — re-confirm it "
                "with the member and regenerate. Not a failure."
            )

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
