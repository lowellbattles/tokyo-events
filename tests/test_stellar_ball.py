"""Tests for the Stellar Ball (ステラボール) scraper.

Fixture: tests/fixtures/stellar_ball_live.html — the real, static schedule
page fetched from https://www.princehotels.co.jp/shinagawa/stellarball/ on
2026-07-13 (3 month slides: July/August/September 2026, 7 events total).
The venue publishes no OPEN/START times or ¥ prices, so those stay None.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.stellar_ball import StellarBallScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.start_date: e
            for e in StellarBallScraper().parse(_load("stellar_ball_live.html"))}


# --- structure ------------------------------------------------------------
def test_parses_exact_event_count():
    # 3 boxes (July) + 3 (August) + 1 (September) = 7, all distinct dates
    assert len(_events()) == 7


def test_empty_html_returns_nothing_loudly():
    # Missing #schedule section must yield 0 events, not silent garbage.
    assert StellarBallScraper().parse("<html></html>") == []
    assert StellarBallScraper().parse("") == []


# --- field spot-checks (copied from visible fixture content) --------------
def test_single_day_event_fields():
    e = _events()["2026-07-10"]
    assert e.title_ja == "最終未来少女 1st TOUR -STELLA-"
    assert e.category == Category.MUSIC
    assert e.end_date is None
    assert e.venue_name == "ステラボール"
    assert e.venue_area == "Shinagawa"
    assert (e.lat, e.lng) == (35.6277449, 139.735559)
    # venue site carries no door/show time and no price
    assert (e.open_time, e.start_time, e.price_min) == (None, None, None)
    # source_url = shared venue page + per-day fragment
    assert e.source_url == (
        "https://www.princehotels.co.jp/shinagawa/stellarball/#2026-07-10")
    # sole outbound link is the promoter's official site
    assert e.ticket_links == [{
        "provider": "official",
        "url": "https://www.saishumiraishoujo.com",
        "code": None}]


def test_multiday_range_and_dark_day():
    # "6.26 Fri. - 7.5 Sun." under the July header -> June start (prev month),
    # July end; the "休演日：6.29 Mon." dark day is captured as a tag.
    e = _events()["2026-06-26"]
    assert "あんさんぶるスターズ" in e.title_ja
    assert e.start_date == "2026-06-26"
    assert e.end_date == "2026-07-05"
    assert "休演日:2026-06-29" in e.tags
    assert e.source_url.endswith("#2026-06-26")


def test_detail_urls_are_absolute_https():
    for e in _events().values():
        assert e.source_url.startswith(
            "https://www.princehotels.co.jp/shinagawa/stellarball/#")
        for link in e.ticket_links:
            assert link["url"].startswith("https://")


def test_second_outbound_link_is_ticket_vendor():
    # #でび夏霞演奏会2026 (9.26) has both an official site and a ＞詳細はこちら
    # ticket page (diskgarage) — both kept, image-only SNS icons dropped.
    e = _events()["2026-09-26"]
    providers = {l["provider"]: l["url"] for l in e.ticket_links}
    assert providers["official"] == "https://devilanthem.net/#/"
    assert providers["diskgarage"] == \
        "https://diskgarage.com/ticket/detail/102206"


# --- category policy (mixed hall calendar) --------------------------------
def test_real_rows_are_all_music():
    # None of the fixture rows match the shared non-music classifier, so every
    # real event stays MUSIC (theatre/musical rows are NOT keyword-guessed).
    assert all(e.category == Category.MUSIC for e in _events().values())


def test_nonmusic_row_is_categorized_other():
    # A synthetic slide whose title trips tu.is_nonmusic must flip to OTHER.
    html = (
        "<section id=schedule><div class=swiper-container><div class=swiper-wrapper>"
        "<div class=swiper-slide>"
        "<div class='head flex'><div class=month>"
        "<p class='gfont en'>August</p><p class='gfont num'>2026.8</p></div></div>"
        "<div class=ctn><div class=box><div class=txt>"
        "<p class=date>8.15 Sat.</p>"
        "<p class=event>ディズニー・オン・アイス 2026</p>"
        "</div></div></div>"
        "</div></div></div></section>")
    evs = StellarBallScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
    assert evs[0].start_date == "2026-08-15"


# --- year resolution across a slide's month boundary ----------------------
def test_year_wrap_for_december_under_january_header():
    # A 12.30 - 1.3 run listed under a January 2027 slide: Dec is 2026, Jan 2027.
    s = StellarBallScraper()
    assert s._mk_date(12, 30, 2027, 1) == dt.date(2026, 12, 30)
    assert s._mk_date(1, 3, 2027, 1) == dt.date(2027, 1, 3)
