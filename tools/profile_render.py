"""Render one validated profile to a standalone HTML document.

Every string in here originates from what an applicant typed into a form —
name, tagline, bio, work titles, captions, alt text, link labels. All of it
is escaped through `_e` before being placed into the page, whether it lands
in a text node or inside an HTML attribute (quotes included). An earlier
audit of this site found a `javascript:` URL reaching the DOM through the
`PCA.orgs` extension point; this module treats every member-supplied field
as the same class of hostile input.

`render_profile` takes `today` as a parameter and never reads a clock —
directly, or through a default argument evaluated at import time — because
the generated HTML must be byte-stable across runs and reproducible from a
recorded date afterwards. Guarded by
tests/test_golden.py::test_today_parameter_is_load_bearing, which is
clock-independent; the byte-stability test alone cannot catch a
reintroduced clock read, since both of its runs happen on the same day.

Nothing here asserts an outcome the project has not observed. The contact
form's copy describes where the message goes, not that it arrives: no
Formspree submission has been confirmed delivered (see FORMSPREE_ID below),
and if the relay is broken a promise of delivery leaves both sender and
member believing a message landed when it did not.
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from tools.profile_schema import Profile, availability_is_current, discipline_by_slug

SITE = "https://philadelphiacreativealliance.com"

# Same endpoint the join forms on index.html post to (commit 3a559d2). Wired, delivery
# unconfirmed — do not describe the contact form as working until a submission is seen.
FORMSPREE_ID = "mbdnqlvo"

STATUS_LABEL = {
    "open": "Taking work now",
    "limited": "Limited availability",
    "closed": "Not taking work",
}


def _e(text: str) -> str:
    return escape(str(text), quote=True)


def _image_name(src: str) -> str:
    return Path(src).stem + ".webp"


def _hero_image(profile: Profile) -> str:
    """The image path for `og:image`: first work wins, else the portrait, else
    `tile.webp`.

    `profile_images.process_profile_images` writes `tile.webp` whenever the
    profile has no portrait at all, works or no works — but this function only
    reaches that fallback in the stricter case where there are neither works
    nor a portrait, because a first work, when present, always wins over the
    tile as the hero image. Every branch here still names a file the pipeline
    actually writes for the same profile.
    """
    if profile.works:
        return _image_name(profile.works[0].src)
    if profile.portrait:
        return _image_name(profile.portrait.src)
    return "tile.webp"


def render_profile(profile: Profile, today: date) -> str:
    base = f"{SITE}/img/artists/{profile.slug}"
    hero = f"{base}/{_hero_image(profile)}"
    url = f"{SITE}/artists/{profile.slug}/"
    labels = [discipline_by_slug(s).label for s in profile.disciplines]
    current = availability_is_current(profile, today)

    tags = "".join(f'<li class="tag">{_e(label)}</li>' for label in labels)
    bio = "".join(f"<p>{_e(para)}</p>" for para in profile.bio)
    open_to = "".join(
        f"<li>{_e(item[:1].upper() + item[1:])}</li>"
        for item in profile.availability.openTo
    )

    badge = ""
    if current:
        badge = (
            f'<p class="badge" data-status="{_e(profile.availability.status)}">'
            f"{_e(STATUS_LABEL[profile.availability.status])}</p>"
        )

    # A label with nothing after it is a broken page, which this renderer is
    # specified to refuse to emit: `updated` is null on some records and
    # absent on others, and `joined` is optional, so both used to ship as
    # "Last confirmed None", a dangling "Last confirmed ", or "Member since ".
    # Fixed here rather than by tightening the loader — a standing ruling
    # keeps availability_is_current lenient about what `updated` contains.
    updated = str(profile.availability.updated or "").strip()
    confirmed = (
        f'<p class="confirmed">Last confirmed {_e(updated)}</p>' if updated else ""
    )
    joined = str(profile.joined or "").strip()
    member_since = f"<span>Member since {_e(joined)}</span>" if joined else ""

    portrait_src = (
        _image_name(profile.portrait.src) if profile.portrait else "tile.webp"
    )
    portrait_alt = profile.portrait.alt if profile.portrait else f"{profile.name} tile"
    portrait = (
        f'<img class="portrait" src="/img/artists/{_e(profile.slug)}/'
        f'{_e(portrait_src)}" alt="{_e(portrait_alt)}">'
    )

    works = ""
    for work in profile.works:
        meta = ", ".join(_e(p) for p in (work.title, work.medium, work.year) if p)
        src = f"/img/artists/{_e(profile.slug)}/{_e(_image_name(work.src))}"
        works += (
            '<figure class="work">'
            f'<button type="button" class="enlarge" data-src="{src}"'
            f' data-alt="{_e(work.alt)}">'
            f'<img src="{src}" alt="{_e(work.alt)}" loading="lazy">'
            "</button>"
            f"<figcaption>{meta}"
            + (f"<span>{_e(work.caption)}</span>" if work.caption else "")
            + "</figcaption></figure>"
        )

    links = "".join(
        f'<li><a href="{_e(link.url)}" rel="noopener">{_e(link.label)}</a></li>'
        for link in profile.links
    )

    # Displayed as copyable source text inside <pre><code>, so it must render as
    # visible markup, never be parsed as markup itself: `_e` turns "<a href=..."
    # into "&lt;a href=..." before it's placed in the document.
    snippet = _e(f'<a href="{url}">Member, Philadelphia Creative Alliance</a>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(profile.name)} — Philadelphia Creative Alliance</title>
<link rel="canonical" href="{_e(url)}">
<meta name="description" content="{_e(profile.tagline)}">
<meta property="og:title" content="{_e(profile.name)}">
<meta property="og:description" content="{_e(profile.tagline)}">
<meta property="og:image" content="{_e(hero)}">
<meta property="og:url" content="{_e(url)}">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/artists/profile.css">
</head>
<body>
<header class="profile-head">
{portrait}
<h1>{_e(profile.name)}</h1>
<ul class="tags">{tags}</ul>
<p class="location">{_e(profile.location)}</p>
{badge}
</header>
<section class="tagline"><p>{_e(profile.tagline)}</p></section>
<section class="bio">{bio}</section>
<section class="works">{works}</section>
<section class="availability">
<h2>Open to</h2>
<ul>{open_to}</ul>
{confirmed}
</section>
<section class="links"><ul>{links}</ul></section>
<section class="contact">
<h2>Get in touch</h2>
<form action="https://formspree.io/f/{FORMSPREE_ID}" method="POST">
<input type="hidden" name="profile" value="{_e(profile.slug)}">
<input type="text" name="_gotcha" tabindex="-1" autocomplete="off" hidden>
<label>Your name <input type="text" name="name" required></label>
<label>Your email <input type="email" name="email" required></label>
<label>Message <textarea name="message" rows="5" required></textarea></label>
<button type="submit">Send</button>
</form>
<p class="relay">This form goes to the Alliance, which passes it on to the member. Their address is not published here.</p>
</section>
<section class="badge-snippet">
<h2>Carry the badge</h2>
<p>Paste this on your own site to link back to the wall.</p>
<pre><code>{snippet}</code></pre>
</section>
<footer>
<a href="/">Philadelphia Creative Alliance</a>
{member_since}
</footer>
<dialog id="lightbox"><img alt=""><form method="dialog"><button>Close</button></form></dialog>
<script>
(function () {{
  var dialog = document.getElementById('lightbox');
  var img = dialog.querySelector('img');
  document.querySelectorAll('.enlarge').forEach(function (button) {{
    button.addEventListener('click', function () {{
      img.src = button.dataset.src;
      img.alt = button.dataset.alt;
      dialog.showModal();
    }});
  }});
}})();
</script>
</body>
</html>
"""
