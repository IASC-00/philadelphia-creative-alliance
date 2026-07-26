"""Turn submitted artist imagery into bounded, checksummed WebP.

Determinism matters here as much as the pixels: the same source image and the
same profile must always produce identical bytes and an identical sha256, so a
later golden/manifest check can trust a rebuild rather than re-hashing. Nothing
below depends on a clock or a random seed introduced in this module — the only
randomness involved is `tools.mosaic`'s own name-seeded RNG, used unchanged.

Rights-cleanliness is the other half: a profile with no submitted imagery at
all never falls back to a stock photo or a placeholder person. It falls back
to a generated mosaic tile from `tools.mosaic.build_org_tile`, deterministically
seeded from the artist's own slug — never a photograph implying a member who
hasn't consented.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from tools.mosaic import build_org_tile, save_asset, sha256_of
from tools.profile_schema import Profile, discipline_by_slug

LONG_EDGE = 1600


def resize_to_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    """Bound the image's long edge to `long_edge`. Never upscales."""
    if max(img.size) <= long_edge:
        return img
    scale = long_edge / max(img.size)
    size = (round(img.width * scale), round(img.height * scale))
    return img.resize(size, Image.LANCZOS)


def fallback_tile(profile: Profile) -> Image.Image:
    """A deterministic mosaic tile for profiles with no submitted imagery.

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
    WebP under `out_root/<slug>/`. A profile with no submitted imagery at all
    gets a single generated fallback tile instead — never a stock photo.

    Returns manifest entries keyed `"<slug>/<filename>.webp"`, each carrying
    `sha256`, `width`, and `height`.
    """
    out_dir = Path(out_root) / profile.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict] = {}

    sources = list(profile.works)
    if profile.portrait:
        sources.append(profile.portrait)

    for item in sources:
        source = Path(src_dir) / item.src
        if not source.exists():
            raise FileNotFoundError(f"{profile.slug}: image not found: {item.src}")
        with Image.open(source) as opened:
            resized = resize_to_long_edge(opened.convert("RGB"), LONG_EDGE)
        target = out_dir / f"{Path(item.src).stem}.webp"
        save_asset(resized, target)
        entries[f"{profile.slug}/{target.name}"] = _manifest_entry(target, resized)

    if not sources:
        tile = fallback_tile(profile)
        target = out_dir / "tile.webp"
        save_asset(tile, target)
        entries[f"{profile.slug}/{target.name}"] = _manifest_entry(target, tile)

    return entries
