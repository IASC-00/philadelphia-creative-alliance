"""Profile taxonomy, schema, and validation for Alliance artist profiles."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
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


_SAFE_STEM = re.compile(r"[A-Za-z0-9._-]+")


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
    """One submitted image entry, validated.

    `src` names a file inside intake/ and nothing else. It is joined onto the
    intake directory verbatim by tools/profile_images.py, so anything with a
    directory component in it — `../originals/piece.jpg`, an absolute path —
    reads an image from outside intake/ and publishes it into
    img/artists/<slug>/, which is *not* gitignored and therefore gets
    committed. The slug got a strict regex after `..` once overwrote the
    homepage; this is the same class of value and needs the same treatment.
    Plausible by operator accident, not only by malice: these paths are
    transcribed by hand alongside the rest of the profile.

    Backslash and colon are rejected explicitly rather than left to
    `Path`. On POSIX `Path(r"..\\originals\\piece.jpg").name` is the *whole
    string*, so a pasted Windows path sails through the separator check —
    then writes a hidden dotfile artefact, and emits an <img src> that the
    WHATWG URL parser resolves with backslash-as-slash, landing outside the
    artist's own directory and 404ing for every visitor while --check stays
    green. Operators do paste Windows paths.

    Types are checked before use: a JSON number or object here otherwise
    raised TypeError/AttributeError out of this module, which callers that
    only catch ProfileError (tools/profiles.py's check()) turn into an
    unhandled traceback rather than a named validation failure.
    """
    if not isinstance(raw, dict):
        raise ProfileError(f"{where} must be an object with src and alt")
    src = raw.get("src", "")
    alt = raw.get("alt", "")
    if not src:
        raise ProfileError(f"{where}: missing src")
    if not isinstance(src, str):
        raise ProfileError(f"{where}: src must be a string, got {type(src).__name__}")
    if not isinstance(alt, str):
        raise ProfileError(f"{where}: alt must be a string, got {type(alt).__name__}")
    if src in (".", "..") or Path(src).name != src or "\\" in src or ":" in src:
        raise ProfileError(
            f"{where}: src {src!r} must be a bare filename inside intake/, "
            "with no directory part"
        )
    if not _SAFE_STEM.fullmatch(Path(src).stem):
        # The published file is <stem>.webp, linked from the profile and the
        # homepage roster; anything else here breaks or drops that link.
        raise ProfileError(
            f"{where}: src {src!r} must use only letters, digits, '.', '_' and '-' "
            "in its name (rename the file in intake/)"
        )
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


def _is_iso_date(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def load_profile(path: Path) -> Profile:
    # Explicit encoding, never the platform default: artist names are the
    # field most likely to carry non-ASCII, and an implicit decode produces
    # mojibake under a non-UTF-8 locale instead of failing loudly.
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    name = _require(data, "name")
    tagline = _require(data, "tagline")
    disciplines = _require(data, "disciplines")
    # Only the JSON literals true and false. A missing flag or the string
    # "false" used to publish a draft (exit-gate 8 Nov, M2).
    if "live" not in data or not isinstance(data["live"], bool):
        raise ProfileError("live must be true or false (JSON, not text)")
    live = data["live"]
    # Consent gates publication, so it is required of every live profile and
    # only of those. A `live: false` record is a draft the artist has not yet
    # agreed to (or a takedown): it is never rendered or listed, and flipping
    # it to live without a full consent block fails here exactly as before.
    # A non-boolean `live` such as the string "false" is truthy, so it lands on
    # the strict side.
    consent = _require(data, "consent") if live else data.get("consent") or {}
    if not isinstance(consent, dict):
        raise ProfileError("consent must be an object")

    if len(tagline) > MAX_TAGLINE:
        raise ProfileError(f"tagline is {len(tagline)} characters, maximum is 90")

    if len(disciplines) < 1:
        raise ProfileError("disciplines: at least 1 required")
    if len(disciplines) > MAX_DISCIPLINES:
        raise ProfileError(f"disciplines: at most {MAX_DISCIPLINES} allowed")
    for slug in disciplines:
        if slug not in DISCIPLINE_SLUGS:
            raise ProfileError(f"unknown discipline: {slug}")

    # Image consent covers the member's own images. A profile that shows none
    # (no works, no portrait) renders only the generated tile, so a member can
    # be listed before they send photos; adding the first image re-arms it.
    # Flags must be the literal true: a sentence such as "Declined; do not
    # list" is non-empty and used to pass (exit-gate 8 Nov, M1). How consent
    # was given goes in its own free-text field, `how`.
    if "works" in data and not isinstance(data["works"], list):
        raise ProfileError("works must be a list")
    shows_images = bool(data.get("works")) or bool(data.get("portrait"))
    if live:
        if consent.get("listed") is not True:
            raise ProfileError("consent: listed must be true")
        if not _is_iso_date(consent.get("date")):
            raise ProfileError("consent: date must be YYYY-MM-DD")
        if shows_images and consent.get("images") is not True:
            raise ProfileError("consent: images must be true when the profile shows images")

    raw_works = data.get("works") or []
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
        live=live,
    )


def slugify(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().strip()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered)
    return hyphenated.strip("-")


# A slug becomes a path segment (artists/<slug>/index.html, img/artists/<slug>/) in
# tools/profiles.py, so it must never contain "/", "..", or anything else a
# filesystem would treat specially. slugify()'s own output always satisfies this
# (it collapses every run of non-alnum characters to a single hyphen and strips
# leading/trailing hyphens), so this check applies uniformly to derived and
# hand-set slugs without narrowing what a derived slug can be.
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def load_all(directory: Path) -> list[Profile]:
    profiles = []
    seen: dict[str, Path] = {}
    for path in sorted(Path(directory).glob("*.json")):
        profile = load_profile(path)
        if profile.slug:
            slug = profile.slug.strip()
        else:
            slug = slugify(profile.name)
        if not slug.strip():
            raise ProfileError(
                f"{path.name}: name produces an empty slug — set one by hand"
            )
        slug = slug.strip()
        if not _SLUG_RE.fullmatch(slug):
            raise ProfileError(
                f"{path.name}: slug {slug!r} must be lowercase letters, digits, "
                "and single hyphens only — it becomes a filesystem path segment"
            )
        if slug in seen:
            raise ProfileError(
                f"slug collision on {slug!r}: {seen[slug].name} and {path.name} — "
                "pick a distinguishing slug by hand"
            )
        seen[slug] = path
        profiles.append(replace(profile, slug=slug))
    return sorted(profiles, key=lambda p: p.name)


STALE_AFTER_DAYS = 90


def availability_is_current(profile: Profile, today: date) -> bool:
    try:
        updated = date.fromisoformat(profile.availability.updated)
    except (ValueError, TypeError):
        return False
    return (today - updated).days <= STALE_AFTER_DAYS
