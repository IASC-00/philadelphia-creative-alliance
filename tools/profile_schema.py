"""Profile taxonomy, schema, and validation for Alliance artist profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Discipline:
    slug: str
    label: str
    hue: str
    t: int


# Hue cycle matches tools/mosaic.py TILE_HUES, in the order index.html already uses.
_CYCLE = ["vermilion", "cobalt", "marigold", "jade", "fuchsia", "violet"]

_LABELS = [
    ("painters", "Painters"),
    ("muralists-street", "Muralists & street artists"),
    ("sculptors", "Sculptors"),
    ("ceramicists", "Ceramicists"),
    ("printmakers-illustrators", "Printmakers & illustrators"),
    ("photographers", "Photographers"),
    ("musicians-sound", "Musicians & sound"),
    ("dancers-performers", "Dancers & performers"),
    ("textile-fashion", "Textile & fashion"),
    ("glass-mixed-media", "Glass & mixed media"),
    ("writers", "Writers"),
    ("filmmakers", "Filmmakers"),
    ("digital-new-media", "Digital & new media"),
    ("game-interactive", "Game & interactive"),
]

DISCIPLINES = [
    Discipline(slug=slug, label=label, hue=_CYCLE[i % 6], t=(i % 6) + 1)
    for i, (slug, label) in enumerate(_LABELS)
]

DISCIPLINE_SLUGS = frozenset(d.slug for d in DISCIPLINES)

_BY_SLUG = {d.slug: d for d in DISCIPLINES}


def discipline_by_slug(slug: str) -> Discipline:
    return _BY_SLUG[slug]
