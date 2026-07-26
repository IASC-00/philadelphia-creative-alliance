#!/usr/bin/env python3
"""
Generative mosaic artwork for the Philadelphia Creative Alliance.

Every image on the site is produced here. Nothing is photographed, licensed, or
traced — which keeps the site rights-clean by construction and means no picture
can ever imply a member organisation that hasn't agreed to be listed.

The visual reference is Philadelphia's own mosaic heritage (Isaiah Zagar's Magic
Gardens, Mural Arts): irregular hand-cut tesserae, wide dark grout, tonal drift
between neighbouring tiles, and a wall that is deliberately unfinished.

Usage:
    python3 tools/mosaic.py            # regenerate everything into img/
    python3 tools/mosaic.py --check    # verify outputs match img/manifest.json

Deterministic on a fixed toolchain: every asset is seeded from its own name, so
a given organisation type always gets the same texture and a rebuild reproduces
the same bytes. That guarantee is scoped -- numpy does not promise Generator
streams are stable across versions (NEP 19), and encoder output moves with
Pillow/libwebp. `--check` compares against img/manifest.json for exactly this
reason, rather than trusting a rebuild to match.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "img"

# --- identity tokens (mirror of the CSS custom properties in index.html) -------
GROUT_LIGHT = (23, 19, 31)  # --ink        #17131F
GROUT_DARK = (20, 16, 25)  # --ground(dk) #141019
PLASTER = (233, 231, 224)  # --ground     #E9E7E0
PAPER = (243, 238, 231)  # --ink(dk)    #F3EEE7

TILE_HUES = {
    "vermilion": (236, 74, 44),  # --t1
    "cobalt": (47, 99, 230),  # --t2
    "marigold": (242, 176, 30),  # --t3
    "jade": (18, 161, 126),  # --t4
    "fuchsia": (219, 47, 134),  # --t5
    "violet": (122, 84, 224),  # --t6
}
PALETTE = list(TILE_HUES.values())

# P052 is URW's Palatino clone, matching the site's --serif token. These paths
# only exist where ghostscript's base-35 fonts are installed, so fall back
# rather than dying on the 5th of 14 assets and leaving img/ half-written.
SERIF_CANDIDATES = (
    "/usr/share/fonts/opentype/urw-base35/P052-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Palatino.ttc",
    "/Library/Fonts/Georgia.ttf",
)
MONO_CANDIDATES = (
    "/usr/share/fonts/opentype/urw-base35/NimbusMonoPS-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)


def load_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise SystemExit(
        "No usable font found. Install ghostscript's base-35 fonts "
        "(Debian/Ubuntu: apt install fonts-urw-base35) or DejaVu.\n"
        f"Looked in: {', '.join(candidates)}"
    )

SS = 3  # supersample factor — render big, downscale for clean tessera edges


def seeded_rng(name: str) -> np.random.Generator:
    """Stable per-asset RNG. Same name always yields the same artwork."""
    digest = hashlib.sha256(name.encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def shift(rgb, dh=0.0, ds=0.0, dv=0.0):
    """Nudge a colour in HSV space. Keeps tiles in-family while varying them."""
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + dh) % 1.0
    s = min(1.0, max(0.0, s + ds))
    v = min(1.0, max(0.0, v + dv))
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def jittered_lattice(cols: int, rows: int, w: int, h: int, jitter: float, rng):
    """
    A grid of points pushed off true. Cutting cells from this gives tesserae that
    read as hand-cut rather than machine-stamped — the single most important
    difference between this and a CSS grid of squares.
    """
    xs = np.linspace(0, w, cols + 1)
    ys = np.linspace(0, h, rows + 1)
    gx, gy = np.meshgrid(xs, ys)
    cell_w, cell_h = w / cols, h / rows
    gx = gx + rng.normal(0, cell_w * jitter, gx.shape)
    gy = gy + rng.normal(0, cell_h * jitter, gy.shape)
    # pin the border so the mosaic fills its frame edge to edge
    gx[:, 0], gx[:, -1] = 0, w
    gy[0, :], gy[-1, :] = 0, h
    return gx, gy


def inset_polygon(pts, amount):
    """Pull a polygon toward its centroid — this is what opens the grout gap."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    out = []
    for x, y in pts:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy) or 1.0
        k = max(0.0, (d - amount) / d)
        out.append((cx + dx * k, cy + dy * k))
    return out


def draw_tessera(draw, pts, color, bevel=True):
    """One tile: flat fill, then a lit top-left edge and a shaded bottom-right."""
    draw.polygon(pts, fill=color)
    if not bevel:
        return
    lit = shift(color, dv=0.13, ds=-0.05)
    shade = shift(color, dv=-0.15)
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        # edges facing up-left catch the light; the rest fall into shadow
        facing_light = (mx - cx) + (my - cy) < 0
        draw.line([a, b], fill=lit if facing_light else shade, width=max(1, SS))


def mosaic_field(
    w: int,
    h: int,
    cols: int,
    rows: int,
    grout: tuple,
    palette: list,
    rng,
    *,
    grout_px: float = 3.0,
    jitter: float = 0.16,
    fill_rate: float = 0.72,
    empty_color: tuple | None = None,
    tonal: bool = False,
    bevel: bool = True,
) -> Image.Image:
    """
    Render one mosaic panel.

    fill_rate below 1.0 leaves tesserae in the ground colour — the unfinished
    wall the whole concept rests on. tonal=True keeps every tile inside one hue
    family, which is what makes a texture readable behind body text.
    """
    W, H = w * SS, h * SS
    img = Image.new("RGB", (W, H), grout)
    draw = ImageDraw.Draw(img)
    gx, gy = jittered_lattice(cols, rows, W, H, jitter, rng)
    empty = empty_color if empty_color is not None else grout
    field = coherence_field(cols, rows, rng)

    for r in range(rows):
        for c in range(cols):
            quad = [
                (gx[r, c], gy[r, c]),
                (gx[r, c + 1], gy[r, c + 1]),
                (gx[r + 1, c + 1], gy[r + 1, c + 1]),
                (gx[r + 1, c], gy[r + 1, c]),
            ]
            pts = inset_polygon(quad, grout_px * SS)

            if rng.random() > fill_rate:
                # left as grout/ground: the wall is deliberately incomplete
                draw_tessera(
                    draw, pts, shift(empty, dv=rng.normal(0, 0.012)), bevel=False
                )
                continue

            base = pick_hue(palette, field[r, c], rng)
            if tonal:
                col = shift(
                    base,
                    dh=rng.normal(0, 0.006),
                    ds=rng.normal(0, 0.05),
                    dv=rng.normal(0, 0.055),
                )
            elif rng.random() < NEUTRAL_RATE:
                # a few muted tesserae. Real mosaics are not uniformly saturated,
                # and the flat ones give the eye somewhere to rest.
                col = shift(base, ds=-0.52, dv=rng.normal(-0.06, 0.05))
            else:
                col = shift(
                    base,
                    dh=rng.normal(0, 0.012),
                    ds=rng.normal(0, 0.07),
                    dv=rng.normal(0, 0.075),
                )
            draw_tessera(draw, pts, col, bevel=bevel)

    img = img.resize((w, h), Image.LANCZOS)
    return add_grain(img, rng, amount=3.0)


NEUTRAL_RATE = 0.14  # share of tesserae rendered desaturated, for tonal relief


def coherence_field(cols: int, rows: int, rng) -> np.ndarray:
    """
    A smooth 0..1 field over the grid, used to bias hue choice by position.

    Picking each tile's colour independently reads as confetti — statistically
    even, visually noise. Sampling a smoothed field instead gives drifts of
    related colour across the panel, which is what makes the result look laid
    by a hand rather than shuffled.
    """
    coarse = rng.random((4, 4)).astype(np.float32)
    smooth = Image.fromarray((coarse * 255).astype(np.uint8), "L").resize(
        (cols, rows), Image.BICUBIC
    )
    arr = np.asarray(smooth).astype(np.float32) / 255.0
    lo, hi = float(arr.min()), float(arr.max())
    return (arr - lo) / (hi - lo) if hi > lo else np.full((rows, cols), 0.5, np.float32)


def pick_hue(palette: list, field_value: float, rng):
    """Regional bias from the field, with enough local noise to avoid banding."""
    if len(palette) == 1:
        return palette[0]
    pos = field_value * len(palette) + rng.normal(0, 0.9)
    return palette[math.floor(pos) % len(palette)]


def add_grain(img: Image.Image, rng, amount=3.0) -> Image.Image:
    """A little surface noise. Without it the tiles look like vector art."""
    arr = np.asarray(img).astype(np.int16)
    noise = rng.normal(0, amount, arr.shape[:2])[:, :, None]
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ------------------------------------------------------------------ assets ----


def build_hero(dark: bool) -> Image.Image:
    name = f"hero-{'dark' if dark else 'light'}"
    rng = seeded_rng(name)
    grout = GROUT_DARK if dark else GROUT_LIGHT
    empty = shift(grout, dv=0.05 if dark else 0.03)
    img = mosaic_field(
        1000,
        1000,
        13,
        13,
        grout,
        PALETTE,
        rng,
        grout_px=3.2,
        jitter=0.17,
        fill_rate=0.70,
        empty_color=empty,
    )
    return img


def build_divider(dark: bool) -> Image.Image:
    name = f"divider-{'dark' if dark else 'light'}"
    rng = seeded_rng(name)
    grout = GROUT_DARK if dark else GROUT_LIGHT
    empty = shift(grout, dv=0.05 if dark else 0.03)
    return mosaic_field(
        1600,
        48,
        60,
        2,
        grout,
        PALETTE,
        rng,
        grout_px=2.0,
        jitter=0.13,
        fill_rate=0.58,
        empty_color=empty,
    )


# Organisation types on the wall. Each gets its own hue and its own seeded
# texture, so no two tiles in the grid look alike.
ORG_TYPES = [
    ("music-sound", "vermilion"),
    ("visual-arts", "cobalt"),
    ("theatre-performance", "marigold"),
    ("film-media", "jade"),
    ("literary-publishing", "fuchsia"),
    ("craft-design", "violet"),
    ("arts-education", "cobalt"),
    ("community-spaces", "vermilion"),
    ("festivals-events", "jade"),
]


def build_org_tile(slug: str, hue_name: str) -> Image.Image:
    """
    Texture that sits behind tile text. Tonal (single hue family) and low
    contrast on purpose — the type has to stay legible over it.
    """
    rng = seeded_rng(f"tile-{slug}")
    base = TILE_HUES[hue_name]
    deep = shift(base, dv=-0.30, ds=0.06)
    return mosaic_field(
        520,
        380,
        9,
        7,
        deep,
        [base],
        rng,
        grout_px=2.4,
        jitter=0.19,
        fill_rate=0.93,
        tonal=True,
    )


def build_og() -> Image.Image:
    """
    1200x630 share card. Regenerated — the v1 card reads "visual & physical
    artists," a scope v2 drops.

    Composition is a gallery poster, not a full-bleed texture: the mosaic runs
    as a band across the top and the type sits on clean grout beneath it. An
    earlier pass laid the wordmark straight over the tiles and it lost every
    legibility contest with the artwork.
    """
    rng = seeded_rng("og-umbrella")
    W, H = 1200, 630
    band_h = 330
    margin = 72

    img = Image.new("RGB", (W, H), GROUT_LIGHT)
    band = mosaic_field(
        W,
        band_h,
        22,
        7,
        GROUT_LIGHT,
        PALETTE,
        rng,
        grout_px=3.0,
        jitter=0.16,
        fill_rate=0.80,
        empty_color=shift(GROUT_LIGHT, dv=0.035),
    )
    img.paste(band, (0, 0))

    draw = ImageDraw.Draw(img)
    accent = (214, 63, 38)  # --accent, light theme

    # hairline between artwork and type, the way a plate sits above a caption
    draw.rectangle([0, band_h, W, band_h + 3], fill=accent)

    serif = load_font(SERIF_CANDIDATES, 74)
    mono_sm = load_font(MONO_CANDIDATES, 21)
    tagline = "GREATER PHILADELPHIA   ·   CREATIVE ORGANISATIONS, ONE ALLIANCE"

    # Lay the type block out from real font metrics. Hand-picked offsets put the
    # tagline through the descenders of "Creative Alliance" on the previous pass.
    chip, gap = 13, 4
    chip_h = chip * 2 + gap
    line_h = serif.getbbox("Hg")[3] - serif.getbbox("Hg")[1]
    tag_h = mono_sm.getbbox(tagline)[3] - mono_sm.getbbox(tagline)[1]

    block_h = chip_h + 22 + line_h + 16 + line_h + 26 + tag_h
    y = band_h + 3 + ((H - band_h - 3) - block_h) / 2

    for i, key in enumerate(["vermilion", "cobalt", "marigold", "fuchsia"]):
        cx = margin + 2 + (i % 2) * (chip + gap)
        cy = y + (i // 2) * (chip + gap)
        draw.rectangle([cx, cy, cx + chip, cy + chip], fill=TILE_HUES[key])
    y += chip_h + 22

    for line in ("Philadelphia", "Creative Alliance"):
        draw.text((margin, y), line, font=serif, fill=PAPER, anchor="lt")
        y += line_h + 16
    y += 10

    draw.text((margin + 2, y), tagline, font=mono_sm, fill=accent, anchor="lt")
    return img


# filename -> (builder, target path). Site imagery ships as WebP: the grain that
# makes the tesserae look like cut stone also defeats PNG's compressor, and WebP
# came out ~10x smaller at visually identical quality (1074 KB -> 125 KB on the
# hero). og.png stays a real PNG — social scrapers are still uneven on WebP — and
# is palette-quantized instead, which the limited mosaic palette takes well.
# filename -> (builder, expected (width, height))
ASSETS = {
    "hero-mosaic-light.webp": (lambda: build_hero(False), (1000, 1000)),
    "hero-mosaic-dark.webp": (lambda: build_hero(True), (1000, 1000)),
    "divider-light.webp": (lambda: build_divider(False), (1600, 48)),
    "divider-dark.webp": (lambda: build_divider(True), (1600, 48)),
    "og.png": (build_og, (1200, 630)),
}
for _slug, _hue in ORG_TYPES:
    ASSETS[f"tile-{_slug}.webp"] = (
        lambda s=_slug, h=_hue: build_org_tile(s, h),
        (520, 380),
    )

WEBP_QUALITY = 82
MANIFEST = IMG / "manifest.json"


def target_path(filename: str) -> Path:
    """og.png sits at the repo root (referenced by absolute URL in the OG tags)."""
    return ROOT / "og.png" if filename == "og.png" else IMG / filename


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_asset(img: Image.Image, target: Path) -> None:
    if target.suffix == ".webp":
        img.save(target, "WEBP", quality=WEBP_QUALITY, method=6)
    else:
        quantized = img.quantize(
            colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG
        )
        quantized.save(target, "PNG", optimize=True)


def generate():
    IMG.mkdir(exist_ok=True)
    total = 0
    manifest = {}
    for filename, (builder, expected) in ASSETS.items():
        target = target_path(filename)
        img = builder()
        if (img.width, img.height) != expected:
            raise SystemExit(
                f"{filename}: builder produced {img.width}x{img.height}, "
                f"declared {expected[0]}x{expected[1]}"
            )
        save_asset(img, target)
        kb = target.stat().st_size / 1024
        total += kb
        manifest[filename] = {
            "sha256": sha256_of(target),
            "width": img.width,
            "height": img.height,
        }
        print(f"  {target.relative_to(ROOT)}  {img.width}x{img.height}  {kb:.0f} KB")
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"  {MANIFEST.relative_to(ROOT)}  ({len(manifest)} entries)")
    print(f"\n  total {total / 1024:.2f} MB across {len(ASSETS)} assets")


def check() -> int:
    """
    Verify the committed assets are the ones this generator produces.

    Existence + "not a flat frame" is not enough on its own: an audit swapped in
    the retired v1 og.png and a 64x64 crop and an earlier version of this check
    passed both. So it compares sha256 and dimensions against the manifest
    written at generation time, which is committed alongside the images.
    """
    failures = []
    total = 0.0
    if not MANIFEST.exists():
        print(
            f"FAILED: {MANIFEST.relative_to(ROOT)} missing — run without --check first"
        )
        return 1
    manifest = json.loads(MANIFEST.read_text())

    for filename, (_builder, expected) in ASSETS.items():
        target = target_path(filename)
        if not target.exists():
            failures.append(f"{filename}: missing")
            continue
        if filename not in manifest:
            failures.append(f"{filename}: absent from manifest")
            continue
        total += target.stat().st_size / 1024
        entry = manifest[filename]
        with Image.open(target) as im:
            im.load()
            spread = float(np.asarray(im.convert("RGB")).std())
            dims = (im.width, im.height)

        if dims != expected:
            failures.append(
                f"{filename}: {dims[0]}x{dims[1]}, expected {expected[0]}x{expected[1]}"
            )
        elif (dims[0], dims[1]) != (entry["width"], entry["height"]):
            failures.append(f"{filename}: dimensions disagree with manifest")
        elif sha256_of(target) != entry["sha256"]:
            failures.append(
                f"{filename}: content does not match manifest (stale or replaced)"
            )
        elif spread < 6.0:
            failures.append(f"{filename}: near-flat image (std {spread:.1f})")
        else:
            print(f"  ok  {filename}  {dims[0]}x{dims[1]}  std {spread:.1f}")

    stray = sorted(set(manifest) - set(ASSETS))
    for s in stray:
        failures.append(f"{s}: in manifest but no longer declared")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print(
        f"\nAll {len(ASSETS)} assets match the manifest ({total / 1024:.2f} MB total)."
    )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify outputs only")
    args = ap.parse_args()
    if args.check:
        sys.exit(check())
    print("Generating mosaic assets...")
    generate()
    print("Done.")
