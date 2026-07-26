# Philadelphia Creative Alliance

The website for the Philadelphia Creative Alliance — an umbrella connecting and supporting
creative organisations across Greater Philadelphia: music, visual arts, theatre and performance,
film and media, literary, craft and design, arts education, community spaces, and festivals.

Individual artists join free.

**Live at** https://philadelphiacreativealliance.com

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
python3 tools/mosaic.py            # rebuild all assets
python3 tools/mosaic.py --check    # verify they exist and aren't blank
```

Requires `pillow` and `numpy`. Output is deterministic — each asset is seeded from its own name,
so a rebuild reproduces the same artwork and a given organisation type always gets the same tile
texture.

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

## Members

Part of the Alliance? Add the badge to your site's footer:

```html
<a href="https://philadelphiacreativealliance.com">Member · Philadelphia Creative Alliance</a>
```
