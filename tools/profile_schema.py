"""Profile taxonomy, schema, and validation for Alliance artist profiles."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path


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


MAX_DISCIPLINES = 3
MAX_WORKS = 12
MAX_TAGLINE = 90
STATUSES = frozenset({"open", "limited", "closed"})


class ProfileError(Exception):
    """A profile that must not be rendered."""


@dataclass(frozen=True)
class Work:
    src: str
    alt: str
    title: str = ""
    year: str = ""
    medium: str = ""
    caption: str = ""


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Availability:
    status: str
    openTo: tuple[str, ...]
    updated: str


@dataclass(frozen=True)
class Profile:
    slug: str
    name: str
    disciplines: tuple[str, ...]
    tagline: str
    bio: tuple[str, ...]
    location: str
    portrait: Work | None
    works: tuple[Work, ...]
    links: tuple[Link, ...]
    availability: Availability
    consent: dict  # Preserved as dict to maintain full data
    joined: str
    live: bool


def _require(data: dict, field: str):
    if field not in data or data[field] in (None, "", {}):
        raise ProfileError(f"missing required field: {field}")
    return data[field]


def _image(raw: dict, where: str) -> Work:
    src = raw.get("src", "")
    alt = raw.get("alt", "")
    if not src:
        raise ProfileError(f"{where}: missing src")
    if not alt.strip():
        raise ProfileError(f"{where}: missing alt text")
    return Work(
        src=src,
        alt=alt,
        title=raw.get("title", ""),
        year=raw.get("year", ""),
        medium=raw.get("medium", ""),
        caption=raw.get("caption", ""),
    )


def load_profile(path: Path) -> Profile:
    data = json.loads(Path(path).read_text())

    name = _require(data, "name")
    tagline = _require(data, "tagline")
    disciplines = _require(data, "disciplines")
    consent = _require(data, "consent")

    if len(tagline) > MAX_TAGLINE:
        raise ProfileError(f"tagline is {len(tagline)} characters, maximum is 90")

    if len(disciplines) < 1:
        raise ProfileError("disciplines: at least 1 required")
    if len(disciplines) > MAX_DISCIPLINES:
        raise ProfileError(f"disciplines: at most {MAX_DISCIPLINES} allowed")
    for slug in disciplines:
        if slug not in DISCIPLINE_SLUGS:
            raise ProfileError(f"unknown discipline: {slug}")

    if not (consent.get("listed") and consent.get("images") and consent.get("date")):
        raise ProfileError("consent: listed, images and date are all required")

    raw_works = data.get("works", [])
    if len(raw_works) > MAX_WORKS:
        raise ProfileError(f"works: at most {MAX_WORKS} allowed")
    works = tuple(_image(w, f"works[{i}]") for i, w in enumerate(raw_works))

    raw_portrait = data.get("portrait")
    portrait = _image(raw_portrait, "portrait") if raw_portrait else None

    links = []
    for raw in data.get("links", []):
        url = raw.get("url", "")
        if not url.startswith(("http://", "https://")):
            raise ProfileError(f"link must be http or https: {url}")
        links.append(Link(label=raw.get("label", url), url=url))

    raw_avail = _require(data, "availability")
    if raw_avail.get("status") not in STATUSES:
        raise ProfileError(f"availability: unknown status {raw_avail.get('status')!r}")

    return Profile(
        slug=data.get("slug", ""),
        name=name,
        disciplines=tuple(disciplines),
        tagline=tagline,
        bio=tuple(data.get("bio", [])),
        location=data.get("location", ""),
        portrait=portrait,
        works=works,
        links=tuple(links),
        availability=Availability(
            status=raw_avail["status"],
            openTo=tuple(raw_avail.get("openTo", [])),
            updated=raw_avail.get("updated", ""),
        ),
        consent=consent,
        joined=data.get("joined", ""),
        live=bool(data.get("live", True)),
    )


def slugify(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().strip()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered)
    return hyphenated.strip("-")


def load_all(directory: Path) -> list[Profile]:
    profiles = []
    seen: dict[str, Path] = {}
    for path in sorted(Path(directory).glob("*.json")):
        profile = load_profile(path)
        slug = profile.slug or slugify(profile.name)
        if not slug:
            raise ProfileError(
                f"{path.name}: name produces an empty slug — set one by hand"
            )
        if slug in seen:
            raise ProfileError(
                f"slug collision on {slug!r}: {seen[slug].name} and {path.name} — "
                "pick a distinguishing slug by hand"
            )
        seen[slug] = path
        profiles.append(replace(profile, slug=slug))
    return sorted(profiles, key=lambda p: p.name)
