"""Turn submitted artist imagery into bounded, checksummed WebP.

Determinism matters here as much as the pixels: the same source image and the
same profile must always produce identical bytes and an identical sha256, so a
later golden/manifest check can trust a rebuild rather than re-hashing. Nothing
below depends on a clock or a random seed introduced in this module — the only
randomness involved is `tools.mosaic`'s own name-seeded RNG, used unchanged.

Rights-cleanliness is the other half: a profile never falls back to a stock
photo or a placeholder person. It falls back to a generated mosaic tile from
`tools.mosaic.build_org_tile`, deterministically seeded from the artist's own
slug — never a photograph implying a member who hasn't consented.

The header portrait is an identity mark, so every profile gets one: a
profile with no submitted portrait gets `tile.webp` for that slot even when
it has works, because a submitted artwork is not a portrait and showing the
same image as both the header and the first work would be redundant. Only a
profile that has a portrait skips the tile.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from tools.mosaic import build_org_tile, save_asset, sha256_of
from tools.profile_schema import Profile, ProfileError, discipline_by_slug

LONG_EDGE = 1600


def resize_to_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    """Bound the image's long edge to `long_edge`. Never upscales."""
    if max(img.size) <= long_edge:
        return img
    scale = long_edge / max(img.size)
    size = (round(img.width * scale), round(img.height * scale))
    return img.resize(size, Image.LANCZOS)


def _normalize_mode(img: Image.Image) -> Image.Image:
    """Preserve a source's real alpha channel; flatten everything else to RGB.

    WebP supports alpha natively and `save_asset` already targets WebP, so a
    transparent background should ship as the artist made it. A blanket
    `.convert("RGB")` instead discards the alpha channel outright — and many
    PNG exporters store black (or otherwise arbitrary) RGB under a fully
    transparent pixel, so that discard reads as a solid opaque background
    where there should be nothing.
    """
    if img.mode in ("RGBA", "LA"):
        return img.convert("RGBA")
    if img.mode == "P" and "transparency" in img.info:
        return img.convert("RGBA")
    return img.convert("RGB")


def fallback_tile(profile: Profile) -> Image.Image:
    """A deterministic mosaic tile for a profile's header slot when it has no
    submitted portrait.

    Seeded from the artist's slug (via `build_org_tile`'s own `tile-{slug}`
    naming) so the same profile always gets the same tile, and hued by the
    artist's first discipline so the wall of fallback tiles isn't monochrome.
    """
    hue = discipline_by_slug(profile.disciplines[0]).hue
    return build_org_tile(f"artist-{profile.slug}", hue)


def _manifest_entry(path: Path, img: Image.Image) -> dict:
    return {
        "sha256": sha256_of(path),
        "width": img.width,
        "height": img.height,
    }


def process_profile_images(
    profile: Profile, src_dir: Path, out_root: Path
) -> dict[str, dict]:
    """Resize and convert one profile's submitted images to WebP.

    Every `Work` in `profile.works` plus `profile.portrait` (if set) is read
    from `src_dir`, bounded to `LONG_EDGE` on its long edge, and written as
    WebP under `out_root/<slug>/`. A profile with no submitted portrait also
    gets a generated `tile.webp` for the header slot — regardless of whether
    it has works — since the header is an identity mark and an artwork is
    not a portrait. A profile that has a portrait never gets a tile.

    Every output's filename (`<stem>.webp` per work/portrait, plus `tile.webp`
    when generated) is checked for collisions with every other output before
    anything is written — two submitted images sharing a stem (`piece.jpg`
    and `piece.png`), or a work named `tile.*` colliding with the generated
    fallback, would otherwise silently overwrite one another. Raises
    `ProfileError` instead: a naming collision among hand-transcribed values
    needs a human decision, the same as a slug collision.

    Returns manifest entries keyed `"<slug>/<filename>.webp"`, each carrying
    `sha256`, `width`, and `height`.
    """
    out_dir = Path(out_root) / profile.slug
    entries: dict[str, dict] = {}

    sources = list(profile.works)
    if profile.portrait:
        sources.append(profile.portrait)
    needs_tile = profile.portrait is None

    # label -> output filename; checked for collisions before any file I/O.
    planned: list[tuple[str, str]] = [
        (item.src, f"{Path(item.src).stem}.webp") for item in sources
    ]
    if needs_tile:
        planned.append(("<generated fallback tile>", "tile.webp"))

    by_name: dict[str, list[str]] = {}
    for label, name in planned:
        by_name.setdefault(name, []).append(label)
    for name, labels in sorted(by_name.items()):
        if len(labels) > 1:
            raise ProfileError(
                f"{profile.slug}: {' and '.join(labels)} all produce {name} — "
                "rename one source by hand"
            )

    out_dir.mkdir(parents=True, exist_ok=True)

    for item in sources:
        source = Path(src_dir) / item.src
        if not source.exists():
            raise FileNotFoundError(f"{profile.slug}: image not found: {item.src}")
        # Only the formats the site publishes from (exit-gate 8 Nov, M3): a
        # crafted PSD/EPS/TIFF in intake/ would otherwise reach Pillow's
        # riskiest decoders on the build machine. Detection is by content,
        # so a renamed file is caught too.
        try:
            with Image.open(source, formats=["JPEG", "PNG", "WEBP"]) as opened:
                resized = resize_to_long_edge(_normalize_mode(opened), LONG_EDGE)
        except UnidentifiedImageError:   # an OSError subclass, so it is caught first
            raise ProfileError(
                f"{profile.slug}: {item.src} must be a JPEG, PNG or WebP image"
            ) from None
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            # Truncated or corrupt data, a directory or unreadable path, or an
            # image too large to decode safely: all stop the build by name.
            raise ProfileError(
                f"{profile.slug}: {item.src} could not be read ({type(exc).__name__})"
            ) from None
        target = out_dir / f"{Path(item.src).stem}.webp"
        save_asset(resized, target)
        entries[f"{profile.slug}/{target.name}"] = _manifest_entry(target, resized)

    if needs_tile:
        tile = fallback_tile(profile)
        target = out_dir / "tile.webp"
        save_asset(tile, target)
        entries[f"{profile.slug}/{target.name}"] = _manifest_entry(target, tile)

    return entries
