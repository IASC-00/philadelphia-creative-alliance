# ruff: noqa: S101
from datetime import date

from tools.profile_render import render_profile
from tools.profile_schema import load_profile

from tests.test_profile_schema import VALID, write_profile

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
    assert "/img/artists/example-artist/tile.webp" in html


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
