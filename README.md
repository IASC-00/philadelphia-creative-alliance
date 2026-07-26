# Philadelphia Creative Alliance

The website for the Philadelphia Creative Alliance — an umbrella connecting and supporting
creative organisations across Greater Philadelphia: music, visual arts, theatre and performance,
film and media, literary, craft and design, arts education, community spaces, and festivals.

Individual artists join free.

**Status: working draft, not launched.** `philadelphiacreativealliance.com` is registered but
still points at registrar parking — the site is not serving there yet. The join forms are wired
but inert until a Formspree ID is set, and donations are off until the Alliance has an entity to
receive them.

## This repo

A single, self-contained static page — `index.html` with inline CSS and vanilla JS, no build step.
Hosted on GitHub Pages (`CNAME` + `.nojekyll`).

```
index.html        the whole site
img/              generated mosaic artwork (WebP)
og.png            share card
tools/mosaic.py   generator for everything in img/ and og.png
```

Local preview:

```bash
python3 -m http.server 8080   # then open http://localhost:8080
```

## Imagery

Every image is generated, never photographed or licensed. Regenerate with:

```bash
python3 tools/mosaic.py            # rebuild all assets + img/manifest.json
python3 tools/mosaic.py --check    # verify assets match the manifest
```

Requires `pillow`, `numpy`, and a serif + monospace font on disk. On Debian/Ubuntu that means
`apt install fonts-urw-base35` (URW's Palatino clone, matching the site's serif); DejaVu and the
macOS system fonts are used as fallbacks.

Output is deterministic on a fixed toolchain — each asset is seeded from its own name, so a
rebuild reproduces the same bytes and a given organisation type always gets the same tile texture.
That guarantee doesn't extend across numpy or Pillow versions, which is why `--check` compares
sha256 and dimensions against the committed `img/manifest.json` rather than trusting a rebuild.

## Adding a member

Members live in the `PCA` config block at the top of the `<script>` in `index.html`. Nothing is
listed publicly until its owner has agreed in writing to appear.

```js
PCA.orgs = [
  { name: 'Example Org', type: 'music-sound', url: 'https://example.org',
    blurb: 'Optional — falls back to the organisation type description.' }
];

PCA.artists = [
  { name: 'A Maker', discipline: 'Ceramicist', url: 'https://example.com' }
];
```

`type` must match a `slug` in `PCA.orgTypes`, which also names its tile texture
(`img/tile-<slug>.webp`). An unrecognised slug is skipped rather than rendered broken. Each
organisation added claims its slot; kinds with no member keep showing an open invitation.

`url` is filtered to `http:`/`https:` before it reaches the DOM — anything else is dropped and the
tile falls back to the join form. Don't remove that check: these values are transcribed by hand
from what an applicant typed.

## Members

Part of the Alliance? Add the badge to your site's footer:

```html
<a href="https://philadelphiacreativealliance.com">Member · Philadelphia Creative Alliance</a>
```
