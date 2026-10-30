# Philadelphia Creative Alliance

The website for the Philadelphia Creative Alliance — an umbrella connecting and supporting
creative organisations across Greater Philadelphia: music, visual arts, theatre and performance,
film and media, literary, craft and design, arts education, community spaces, and festivals.

Individual artists join free.

**Status: working draft, not launched.** `philadelphiacreativealliance.com` is registered but
still points at registrar parking — the site is not serving there yet. The join forms are wired
to a Formspree endpoint, delivery unconfirmed — no submission has been seen arriving, so don't
describe them as working. Donations are off until the Alliance has an entity to receive them.

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

Nothing is listed publicly until its owner has agreed in writing to appear.

**Organisations** are hand-edited, in the `PCA` config block at the top of the `<script>` in
`index.html`:

```js
PCA.orgs = [
  { name: 'Example Org', type: 'music-sound', url: 'https://example.org',
    blurb: 'Optional — falls back to the organisation type description.' }
];
```

`type` must match a `slug` in `PCA.orgTypes`, which also names its tile texture
(`img/tile-<slug>.webp`). An unrecognised slug is skipped rather than rendered broken. Each
organisation added claims its slot; kinds with no member keep showing an open invitation.

`url` is filtered to `http:`/`https:` before it reaches the DOM — anything else is dropped and the
tile falls back to the join form. Don't remove that check: these values are transcribed by hand
from what an applicant typed.

**Artists are not hand-edited.** `PCA.artists` and `PCA.disciplines` sit between `// GENERATED`
markers and are rewritten from `profiles/*.json` every time `tools/profiles.py` runs — an entry
added by hand there renders a blank discipline and is erased on the next build. Add an artist by
writing a profile and running the generator; see [Artist profiles](#artist-profiles) below.

## Members

Part of the Alliance? Add the badge to your site's footer:

```html
<a href="https://philadelphiacreativealliance.com">Member · Philadelphia Creative Alliance</a>
```

## Artist profiles

Profiles are data. `profiles/<slug>.json` is the source of truth; `tools/profiles.py`
renders `artists/<slug>/index.html`, processes imagery into `img/artists/<slug>/`, and
rewrites the `disciplines` and `artists` blocks in `index.html` between their
`// GENERATED` markers.

    python3 tools/profiles.py           # build
    python3 tools/profiles.py --check   # verify committed output is current

To add an artist: write `profiles/<slug>.json`, drop their submitted images in `intake/`,
run the generator, and commit what it produced. `disciplines` is an array of one to three
slugs from the taxonomy in `tools/profile_schema.py` — not a free-text label. Each image
`src` is a bare filename in `intake/`, never a path. A profile without a recorded `consent`
block will not build.

Submitted originals stay in `intake/`, which is gitignored — only the processed WebP is
published. Because of that, `--check` can only rebuild and compare imagery where those
originals are on disk; elsewhere (a fresh clone, CI) it compares the committed manifest
against what is on disk and says so. Page HTML comes from the committed JSON alone, so it
is always rebuilt and compared.

**Drafts for an artist to review** go outside the repo, never in `profiles/`. The repo is
public and Pages serves every committed file, so a draft's JSON in `profiles/` is readable on
the web even with `live: false`. Write the draft with `live: false` (a draft needs no
`consent` block; a live profile still does), then render a review copy:

    python3 tools/profiles.py --preview ~/somewhere/out --profiles-dir ~/somewhere/drafts --intake-dir ~/somewhere/intake

Each review page opens with "Draft — for the artist's review; not published" and its contact
form is switched off. `--preview` refuses any output folder inside a git work tree. Serve the
output folder as a web root to view it (pages use the site's absolute paths).

Every profile page carries `noindex, nofollow`, matching the homepage until launch.

**To take a member down, set `live: false` on their profile and re-run the generator.** That
is what removes their page and images. Deleting the record instead leaves the published page
and photos in place — the generator warns and `--check` reports the page as an orphan.
