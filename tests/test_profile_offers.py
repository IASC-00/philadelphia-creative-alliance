# ruff: noqa: S101
"""Offers: what a member can be booked or bought for (the shop half of a profile).

Every field is the member's own words, including the price, which is free text
("From $400", "Ask for a quote") and never parsed or invented by the site.
"""
from datetime import date

import pytest

from tools.profile_render import render_profile
from tools.profile_schema import MAX_OFFERS, ProfileError, load_profile

from tests.test_profile_schema import write_profile

TODAY = date(2026, 11, 13)

TWO = [
    {"title": "DJ sets for private events", "detail": "Weddings, parties, launches.", "price": "Ask for a quote"},
    {"title": "Club nights"},
]


def test_offers_are_optional(tmp_path):
    assert load_profile(write_profile(tmp_path)).offers == ()


def test_offers_load_in_order_with_optional_fields_blank(tmp_path):
    offers = load_profile(write_profile(tmp_path, offers=TWO)).offers
    assert [o.title for o in offers] == ["DJ sets for private events", "Club nights"]
    assert offers[0].price == "Ask for a quote"
    assert offers[1].detail == "" and offers[1].price == ""


@pytest.mark.parametrize(
    "offers, message",
    [
        ("DJ sets", "offers must be a list"),
        (["DJ sets"], r"offers\[0\] must be an object"),
        ([{"detail": "no title"}], r"offers\[0\]: missing title"),
        ([{"title": "   "}], r"offers\[0\]: missing title"),
        ([{"title": 5}], r"offers\[0\]: title must be text"),
        ([{"title": "x", "price": 400}], r"offers\[0\]: price must be text"),
        ([{"title": "x", "detail": ["a"]}], r"offers\[0\]: detail must be text"),
        ([{"title": "x" * 61}], r"offers\[0\]: title is 61 characters, maximum is 60"),
        ([{"title": "x", "detail": "d" * 241}], r"offers\[0\]: detail is 241 characters, maximum is 240"),
        ([{"title": "x", "price": "p" * 41}], r"offers\[0\]: price is 41 characters, maximum is 40"),
        ([{"title": "Same"}, {"title": "same "}], r"offers\[1\]: duplicate title"),
    ],
)
def test_bad_offers_are_named_errors(tmp_path, offers, message):
    with pytest.raises(ProfileError, match=message):
        load_profile(write_profile(tmp_path, offers=offers))


def test_offer_count_is_capped(tmp_path):
    many = [{"title": f"Offer {i}"} for i in range(MAX_OFFERS + 1)]
    with pytest.raises(ProfileError, match=f"at most {MAX_OFFERS}"):
        load_profile(write_profile(tmp_path, offers=many))
    load_profile(write_profile(tmp_path, offers=many[:MAX_OFFERS]))


def test_no_offers_renders_no_shop_and_the_plain_contact_form(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path)), TODAY)
    assert 'class="offers"' not in html
    assert 'name="offer"' not in html
    assert "<h2>Get in touch</h2>" in html


def test_offers_render_as_a_shop_with_a_request_button_each(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY)
    assert '<section class="offers" id="offers" aria-labelledby="offers-h">' in html
    assert html.count('<li class="offer">') == 2
    assert '<h3 id="offer-0">DJ sets for private events</h3>' in html
    assert '<p class="price">Ask for a quote</p>' in html
    # An offer without a price shows no empty price line.
    assert html.count('class="price"') == 1
    assert html.count('class="ask"') == 2
    assert 'href="#contact" data-offer="Club nights" aria-describedby="offer-1"' in html


def test_offers_feed_a_select_in_the_contact_form(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY)
    assert '<section class="contact" id="contact">' in html
    assert "<h2>Request to book</h2>" in html
    assert '<select name="offer" id="offer-pick">' in html
    assert '<option value="DJ sets for private events">DJ sets for private events</option><option value="Club nights">Club nights</option><option value="other">Something else</option>' in html


def test_offer_text_is_escaped_everywhere_it_lands(tmp_path):
    evil = [{"title": '"><script>x</script>', "detail": "<b>d</b>", "price": "<i>p</i>"}]
    html = render_profile(load_profile(write_profile(tmp_path, offers=evil)), TODAY)
    assert "<script>x</script>" not in html
    assert "<b>d</b>" not in html and "<i>p</i>" not in html
    assert 'data-offer="&quot;&gt;&lt;script&gt;x&lt;/script&gt;"' in html
    assert '<option value="&quot;&gt;&lt;script&gt;x&lt;/script&gt;">&quot;&gt;&lt;script&gt;x&lt;/script&gt;</option>' in html


def test_draft_copy_keeps_the_offers_but_cannot_send(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY, draft=True)
    assert '<li class="offer">' in html
    assert "<form>" in html and "disabled" in html


def test_select_defaults_to_no_offer_so_general_messages_are_not_mislabelled(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY)
    assert '<select name="offer" id="offer-pick"><option value="" selected>Choose one (optional)</option>' in html


def test_options_carry_explicit_values_matching_data_offer(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY)
    assert '<option value="Club nights">Club nights</option>' in html
    assert 'data-offer="Club nights"' in html
    assert '<option value="other">Something else</option>' in html


def test_inner_whitespace_is_collapsed_so_select_and_duplicates_agree(tmp_path):
    offers = load_profile(write_profile(tmp_path, offers=[{"title": "DJ  sets\nfor\tparties", "detail": "a  b", "price": "From\n$400"}])).offers
    assert offers[0].title == "DJ sets for parties"
    assert offers[0].detail == "a b" and offers[0].price == "From $400"
    with pytest.raises(ProfileError, match=r"offers\[1\]: duplicate title"):
        load_profile(write_profile(tmp_path, offers=[{"title": "DJ sets"}, {"title": "DJ  sets"}]))


@pytest.mark.parametrize("title", ["Something else", "something  ELSE", "other"])
def test_catch_all_names_are_reserved(tmp_path, title):
    with pytest.raises(ProfileError, match=r"offers\[0\]: .* is reserved"):
        load_profile(write_profile(tmp_path, offers=[{"title": title}]))


def test_unknown_offer_keys_are_rejected(tmp_path):
    with pytest.raises(ProfileError, match=r"offers\[0\]: unknown field 'prices'"):
        load_profile(write_profile(tmp_path, offers=[{"title": "x", "prices": "$5"}]))


def test_each_request_link_is_described_by_its_offer(tmp_path):
    html = render_profile(load_profile(write_profile(tmp_path, offers=TWO)), TODAY)
    assert '<h3 id="offer-1">Club nights</h3>' in html
    assert 'aria-describedby="offer-1"' in html
