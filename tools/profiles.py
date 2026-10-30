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
    _SLUG_RE,
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
    """Prune artists/<slug>/, img/artists/<slug>/ and any leftover
    .<slug>.incoming staging, for slugs this tool has previously published
    (`known_slugs`) that are no longer live — removed entirely, or flipped to
    `live: false`.

    Driven by `known_slugs`, not by walking artists/. Walking artists/ made
    the *page* directory the key to everything else the same member had
    published: once it was gone — removed by hand on this tool's own advice,
    by a prune that failed after removing it, or by a SIGKILL — their images
    and staging could never be reached again. They stayed served, --check
    stayed red, and every route the tool printed was a no-op. The manifest is
    the record of what this tool published, so the manifest is what the prune
    reads; a slug it knows is prunable regardless of which of that slug's
    directories still exist.

    Images before the page. A failure between the two halves then leaves the
    page — the half a re-run rebuilds from committed JSON — rather than
    photographs with no page in front of them.

    A per-slug failure is reported and the loop continues, so one member's
    unremovable directory cannot keep another member published. The first
    error is re-raised once the loop is done: that raise leaves generate()
    before it writes the manifest, so the previously committed manifest
    survives untouched and still names every slug — including the one that
    failed. That is what lets the next run retry, and it is why refusing to
    remove something must always raise rather than pass quietly. A prune that
    declined silently would be followed by a manifest write that no longer
    named the member, and no later run could ever reach them again.

    Four guards, each closing a distinct way this pass could destroy
    something it shouldn't:
      - `known_slugs` scoping: a directory that was never in a previously
        committed manifest (artists/profile.css's directory, a hand-made
        artists/_shared/, ...) is never touched, regardless of its name.
      - the slug regex: `known_slugs` is derived from manifest keys, which
        are text, and those keys are now joined onto `root` rather than
        compared against directories that already exist. A hand-edited or
        corrupted manifest must not be able to name `../..` — the slug got
        its regex in tools/profile_schema.py after `..` once overwrote the
        homepage, and the same shape is required here before anything is
        joined.
      - `is_symlink()`, checked before `is_dir()` in `_remove_dir`, because
        `Path.is_dir()` follows symlinks and would otherwise let a symlinked
        directory through straight into `shutil.rmtree`, which raises on a
        symlink rather than silently doing something safe.
      - `is_dir()`: the page and image directories are only ever removed when
        they are plain directories — artists/profile.css is a file living
        beside them. Anything else there is refused *loudly*, per above. The
        one exception is `.<slug>.incoming`: that path is this tool's own
        namespace, nothing else can produce it, and it is removed whatever it
        turns out to be.
    """
    failures: list[OSError] = []
    for slug in sorted(known_slugs - live_slugs):
        if not _SLUG_RE.fullmatch(slug):
            # Not a slug this tool can have produced, so nothing here is ours
            # to remove — but say so rather than dropping it in silence.
            print(f"  ignored manifest entry for {slug!r}: not a slug this tool writes")
            continue
        images = root / "img" / "artists" / slug
        removed: list[str] = []
        error: OSError | None = None
        try:
            if _remove_dir(images):
                removed.append(f"img/artists/{slug}")
            # Any staging directory a previously interrupted swap left behind
            # for this slug: it holds the same member's artwork and is served
            # just as publicly as the real directory.
            if _remove_path(_staging_path(images)):
                removed.append(f"img/artists/.{slug}.incoming")
            if _remove_dir(root / "artists" / slug):
                removed.append(f"artists/{slug}")
        except OSError as exc:
            error = exc
            failures.append(exc)
        # Printed even when this slug failed part-way: a removal that did
        # happen is reported as having happened.
        for name in removed:
            print(f"  removed {name}")
        if error is not None:
            print(f"  FAILED to remove {slug}: {error}")
    if failures:
        raise failures[0]


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


def _remove_path(target: Path) -> bool:
    """Remove `target` whatever it is, and do nothing if it isn't there.
    Reports whether anything was actually removed, so the prune only ever
    prints that it removed something it did remove.

    `is_symlink()` is checked before `is_dir()` because `Path.is_dir()`
    follows symlinks, and `shutil.rmtree` raises on a symlink rather than
    doing anything safe — the symlink itself is what has to go.
    """
    if target.is_symlink() or target.is_file():
        target.unlink()
        return True
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return False


def _remove_dir(target: Path) -> bool:
    """Remove `target` when it is a plain directory and report that it went;
    return False when there is nothing there at all; raise when something *is*
    there that this must not remove — a symlink, or a plain file.

    The prune uses this rather than `_remove_path` because both refusals are
    load-bearing there: artists/profile.css is a *file* living beside the
    profile directories, and a symlink is not this tool's to follow into
    `shutil.rmtree` (which raises on one) nor to unlink.

    Refusing *loudly* is the part that matters. Returning False for something
    that exists reads to the caller as "already gone", and the prune's caller
    goes on to write a manifest that no longer names the slug — so a member
    whose img/artists/<slug> happened to be a file or a symlink would be
    forgotten by every future run while their bytes stayed served, and
    --check would pass. A raise leaves the committed manifest intact and the
    gate red, which is recoverable.
    """
    if target.is_symlink():
        raise OSError(f"{target} is a symlink — remove it by hand")
    if not target.exists():
        return False
    if not target.is_dir():
        raise NotADirectoryError(f"{target} is not a directory — remove it by hand")
    shutil.rmtree(target)
    return True


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
                "fails — the manifest still lists them and nothing produces "
                'them — until the record is restored with "live": false and '
                "this generator re-run, which then removes the page and the "
                "images together. Removing the page yourself is not a "
                "takedown: it takes down the page and leaves the photographs "
                "served.\n"
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


def _orphaned_page_failures(
    root: Path, committed: dict, prune_enabled: bool = True
) -> list[str]:
    """Anything published that no manifest entry accounts for — a page under
    artists/, or an image directory under img/artists/.

    Without this, deleting a member's record — the natural response to
    "please remove me" — could pass green: the prune is skipped when no
    profile files remain, so the page and photos stay on disk while nothing
    in the manifest still refers to them.

    Both halves are anchored on what is actually *served*, not on a directory
    existing: a published index.html under artists/, an image file of any
    format under img/artists/. A hand-made artists/_shared/ or
    img/artists/_shared/ holding notes or a stylesheet is one the prune is
    forbidden to touch, so reporting it is a red gate with no action behind
    it. The image half used to lack that exemption. It is deliberately not
    narrowed to the `.webp` this tool writes: the sentence it prints is
    "these are still being served", and a stray JPEG under img/artists/ is
    served exactly as a WebP is.

    The image half exists because removing an orphaned *page* leaves the
    photos published at img/artists/<slug>/ and the gate green, so the
    de-listed member's work is still being served. It also catches
    `.<slug>.incoming` staging directories left by an interrupted swap, which
    are published the same way.

    What each failure tells the operator to do is decided by what the
    manifest knows and by whether the prune can run at all, because together
    those decide what the generator is able to do. Anything reported here is
    by definition absent from the manifest, and the prune may never remove
    what the manifest does not record it published — so naming a
    `live: false` re-run would name an action that provably does nothing.
    The exception is staging for a slug the manifest *does* know, which a
    re-run does clear — but only while there is at least one profile record
    on disk, since generate() skips the prune entirely when there are none.
    `prune_enabled` carries that in, so this never tells an operator to
    re-run a pass that will not run.
    """
    root = Path(root)
    failures = []
    accounted = {key.split("/", 1)[0] for key in committed if "/" in key}

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
                    "the manifest — an orphaned page. The generator will not "
                    "remove it: it only ever deletes what the manifest records "
                    "it published, and this is not in the manifest. If the "
                    "member's record is still there and live, re-run the "
                    "generator — that republishes the page and records it. "
                    f"Otherwise remove the directory by hand (rm -r artists/"
                    f"{entry.name}/), and img/artists/{entry.name}/ with it if "
                    "their photographs are still there."
                )

    images_root = root / "img" / "artists"
    if images_root.is_dir():
        for entry in sorted(images_root.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            staged_for = _staged_slug(entry.name)
            if staged_for is None and entry.name in accounted:
                continue
            if not _holds_images(entry):
                continue
            if staged_for is not None:
                what = (
                    f"img/artists/{entry.name}/: leftover staging from an "
                    "interrupted publish — the same member's artwork, served "
                    "just as publicly as the real directory."
                )
            else:
                what = (
                    f"img/artists/{entry.name}/: published images absent from "
                    "the manifest — an orphaned image directory. These are "
                    "still being served."
                )
            if staged_for is not None and staged_for in accounted and prune_enabled:
                remedy = (
                    "Re-run the generator: it clears this slug's staging "
                    "either way — the next publish replaces it while the "
                    "member is live, and the prune removes it once they are "
                    "not."
                )
            elif staged_for is not None and staged_for in accounted:
                remedy = (
                    "No profile records remain, so the generator skips its "
                    "prune pass by design and a re-run will not clear this. "
                    "Put the member's record back in profiles/ with "
                    '"live": false and re-run — that removes this staging '
                    "along with their page and images. Removing the "
                    "directory by hand works too."
                )
            else:
                remedy = (
                    "The generator will not remove it: it only ever deletes "
                    "what the manifest records it published, and this is not "
                    "in the manifest. If the member's record is still there "
                    "and live, re-run the generator — that republishes and "
                    "records these. Otherwise remove the directory by hand "
                    f"(rm -r img/artists/{entry.name}/)."
                )
            failures.append(f"{what} {remedy}")
    return failures


def _staged_slug(name: str) -> str | None:
    """The slug a `.<slug>.incoming` staging directory belongs to, or None
    when `name` is not one of those. See `_staging_path`."""
    suffix = ".incoming"
    if name.startswith(".") and name.endswith(suffix) and len(name) > len(suffix) + 1:
        return name[1 : -len(suffix)]
    return None


IMAGE_SUFFIXES = frozenset(
    {
        ".webp",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".avif",
        ".svg",
        ".bmp",
        ".tif",
        ".tiff",
    }
)


def _holds_images(directory: Path) -> bool:
    """Whether `directory` holds imagery that a visitor would be served.

    This is the image-side equivalent of the page half's "is there a
    published index.html in it" anchor, and the reason a hand-made
    img/artists/_shared/ holding notes or a stylesheet is not called an
    orphan while the prune is forbidden to touch it.

    Deliberately wider than the `.webp` this tool writes. The failure it
    guards says "these are still being served", and GitHub Pages serves a
    JPEG or a PNG dropped under img/artists/ exactly as it serves a WebP —
    anchoring on this tool's own output format would have made a directory
    full of somebody's photographs invisible to the gate. Suffixes are
    compared lowercased, so PHOTO.JPG counts.

    An unreadable directory reports True. Nothing can be ruled out about
    what it serves, and this is called from inside --check's failure
    collection: raising here would abandon the whole report, including
    failures already found, and print nothing at all.
    """
    try:
        return any(
            child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES
            for child in directory.iterdir()
        )
    except OSError:
        return True


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

    # `bool(_all)` is exactly the condition generate() prunes on, so the
    # remedies below never name a pass that will not run.
    failures.extend(_orphaned_page_failures(root, committed, bool(_all)))
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


def _inside_a_git_work_tree(path: Path) -> Path | None:
    """The first directory at or above `path` that holds a `.git`, or None."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def preview(profiles_dir: Path, src_dir: Path, out: Path, today: date) -> int:
    """Render every profile in `profiles_dir` — live or not — as a draft review
    copy under `out`, for the artist to read before they agree to be listed.

    Never writes into the site. `out` must be outside every git work tree: the
    repo is public and Pages serves every committed file (.nojekyll), so a
    preview that landed in the tree would be one `git add` away from being
    published. For the same reason draft profile JSON belongs outside the repo
    too (pass it with --profiles-dir); `live: false` keeps a record off the
    wall and out of artists/, but not the record itself out of a public repo.

    Pages use the site's absolute paths (/artists/profile.css,
    /img/artists/<slug>/...), so serve `out` as a web root to view them.
    """
    out = Path(out).resolve()
    repo = _inside_a_git_work_tree(out)
    if repo is not None:
        print(f"Refusing to preview into {out}: it is inside the git work tree {repo}.")
        return 2
    try:
        profiles = load_all(profiles_dir)
    except ProfileError as exc:
        print(f"FAILED: {exc}")
        return 1
    if not profiles:
        print(f"No profiles found in {profiles_dir}.")
        return 1
    (out / "artists").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "artists" / "profile.css", out / "artists" / "profile.css")
    for profile in profiles:
        process_profile_images(profile, src_dir, out / "img" / "artists")
        page = out / "artists" / profile.slug / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(render_profile(profile, today, draft=True), encoding="utf-8")
        state = "live" if profile.live else "not live"
        print(f"  draft {profile.slug} ({state}) -> {page}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify outputs only")
    mode.add_argument(
        "--preview",
        metavar="OUT",
        type=Path,
        help="render every profile as a draft review copy into OUT (outside the repo)",
    )
    ap.add_argument(
        "--profiles-dir", type=Path, help="with --preview: read profiles from here"
    )
    ap.add_argument(
        "--intake-dir", type=Path, help="with --preview: read images from here"
    )
    args = ap.parse_args()
    if args.preview is None and (args.profiles_dir or args.intake_dir):
        ap.error("--profiles-dir and --intake-dir only apply to --preview")
    if args.preview is not None:
        sys.exit(
            preview(
                args.profiles_dir or PROFILES_DIR,
                args.intake_dir or INTAKE_DIR,
                args.preview,
                date.today(),
            )
        )
    runner = check if args.check else generate
    sys.exit(runner(PROFILES_DIR, INTAKE_DIR, ROOT, date.today()))
