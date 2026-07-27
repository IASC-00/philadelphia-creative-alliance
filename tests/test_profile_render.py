# ruff: noqa: S101
import re
from datetime import date
from pathlib import Path

from PIL import Image

from tools.profile_images import process_profile_images
from tools.profile_render import render_profile
from tools.profile_schema import load_profile

from tests.test_profile_schema import write_profile

REPO = Path(__file__).resolve().parent.parent

TODAY = date(2026, 7, 26)


def test_renders_a_complete_document(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert html.startswith("<!doctype html>")
    assert "Example Artist" in html
    assert "Digital &amp; new media" in html


def test_escapes_markup_in_the_name(tmp_path):
    profile = load_profile(write_profile(tmp_path, name="<script>alert(1)</script>"))
    html = render_profile(profile, TODAY)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_escapes_markup_in_bio_and_captions(tmp_path):
    works = [{"src": "w.jpg", "alt": "a", "caption": "<img onerror=x>"}]
    profile = load_profile(write_profile(tmp_path, bio=["<b>hi</b>"], works=works))
    html = render_profile(profile, TODAY)
    assert "<b>hi</b>" not in html
    assert "<img onerror=x>" not in html


def test_escapes_quotes_in_alt_text(tmp_path):
    works = [{"src": "w.jpg", "alt": 'x" onerror="alert(1)'}]
    profile = load_profile(write_profile(tmp_path, works=works))
    assert 'onerror="alert(1)"' not in render_profile(profile, TODAY)


def test_current_availability_shows_the_badge(tmp_path):
    availability = {"status": "open", "openTo": ["teaching"], "updated": "2026-07-26"}
    html = render_profile(
        load_profile(write_profile(tmp_path, availability=availability)), TODAY
    )
    assert "Taking work now" in html
    assert "Teaching" in html


def test_open_to_preserves_the_applicants_own_casing(tmp_path):
    availability = {
        "status": "open",
        "openTo": ["AI residencies"],
        "updated": "2026-07-26",
    }
    html = render_profile(
        load_profile(write_profile(tmp_path, availability=availability)), TODAY
    )
    assert "AI residencies" in html
    assert "Ai residencies" not in html


def test_stale_availability_drops_the_badge_but_keeps_open_to(tmp_path):
    availability = {"status": "open", "openTo": ["teaching"], "updated": "2025-01-01"}
    html = render_profile(
        load_profile(write_profile(tmp_path, availability=availability)), TODAY
    )
    assert "Taking work now" not in html
    assert "Teaching" in html


def test_og_image_points_at_the_first_work(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert (
        'content="https://philadelphiacreativealliance.com'
        '/img/artists/example-artist/work-01.webp"' in html
    )


def test_og_image_falls_back_to_the_tile(tmp_path):
    profile = load_profile(write_profile(tmp_path, works=[], portrait=None))
    html = render_profile(profile, TODAY)
    assert (
        'content="https://philadelphiacreativealliance.com'
        '/img/artists/example-artist/tile.webp"' in html
    )


def test_og_image_uses_the_portrait_when_there_are_no_works(tmp_path):
    profile = load_profile(write_profile(tmp_path, works=[]))
    html = render_profile(profile, TODAY)
    assert (
        'content="https://philadelphiacreativealliance.com'
        '/img/artists/example-artist/portrait.webp"' in html
    )


def test_renders_the_portrait_when_present(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert '/img/artists/example-artist/portrait.webp"' in html
    assert 'alt="A portrait."' in html


def test_renders_the_fallback_tile_when_there_is_no_portrait(tmp_path):
    profile = load_profile(write_profile(tmp_path, portrait=None))
    assert "/img/artists/example-artist/tile.webp" in render_profile(profile, TODAY)


def test_contact_form_never_exposes_an_email_address(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert "mailto:" not in html
    assert 'name="profile"' in html
    assert 'value="example-artist"' in html


def test_contact_form_posts_to_the_configured_endpoint(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert 'action="https://formspree.io/f/mbdnqlvo"' in html


def test_badge_snippet_is_escaped_not_executable(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert "&lt;a href=" in html
    assert "Member, Philadelphia Creative Alliance" in html


def test_footer_carries_member_since(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert "Member since 2026-07-26" in html


def test_works_are_enlargeable_with_a_native_dialog(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert "<dialog" in html
    assert "showModal()" in html


def test_header_image_matches_what_the_pipeline_writes_when_only_works_exist(tmp_path):
    # Works present, portrait absent: the pipeline (tools.profile_images) now
    # writes tile.webp for the header slot in this exact case, not only when a
    # profile has no imagery at all. This pins the renderer's portrait-fallback
    # path to the filename the pipeline actually produces for the same profile,
    # rather than each module's naming contract being verified in isolation.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    Image.new("RGB", (400, 300), (10, 20, 30)).save(src_dir / "work-01.jpg")

    profile = load_profile(write_profile(tmp_path, portrait=None))
    entries = process_profile_images(profile, src_dir, tmp_path / "out")
    written_names = {name.split("/", 1)[1] for name in entries}
    assert written_names == {"work-01.webp", "tile.webp"}

    html = render_profile(profile, TODAY)
    match = re.search(r'<img class="portrait" src="[^"]+/([^"/]+)"', html)
    assert match is not None
    assert match.group(1) in written_names


def test_stylesheet_exists_and_defines_both_themes():
    css = (REPO / "artists" / "profile.css").read_text()
    assert "--grout-ink" in css
    assert "prefers-color-scheme: dark" in css
    assert '[data-theme="dark"]' in css
    assert '[data-theme="light"]' in css


def test_stylesheet_covers_every_class_the_renderer_emits():
    css = (REPO / "artists" / "profile.css").read_text()
    for selector in (
        ".portrait",
        ".tags",
        ".badge",
        ".work",
        ".enlarge",
        ".availability",
        ".contact",
        ".relay",
        ".badge-snippet",
        "dialog",
    ):
        assert selector in css, f"stylesheet has no rule for {selector}"
