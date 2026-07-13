"""Tests for the LOFT HEAVEN hall, added to the Loft Project family.

Fixture `loft_heaven_live.html` is the real /schedule/heaven/ short listing
(the page LoftScraper.scrape() fetches), saved 2026-07-13. HEAVEN reuses the
byte-identical WordPress template already parsed for Shinjuku LOFT / SHELTER,
so the shared LoftScraper.parse() handles it unchanged — these tests pin that
it does, plus HEAVEN's venue metadata and detail-URL shape.

The existing loft/shelter assertions live in tests/test_scrapers.py and are
intentionally untouched.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.loft import LoftScraper

FIX = Path(__file__).parent / "fixtures"
# Full year/month/day is in the listing, so year inference is a no-op; pin
# 'today' anyway for determinism/style parity with the other scraper tests.
TODAY = dt.date(2026, 7, 13)


def _heaven():
    html = (FIX / "loft_heaven_live.html").read_text(encoding="utf-8")
    return {e.source_url.rstrip("/").split("/")[-1]: e
            for e in LoftScraper("loft_heaven").parse(html, today=TODAY)}


def test_heaven_parses_all_events():
    # 14 event cards on the fixture; the 2 "news_column" pickup cards carry
    # /schedule/news/... hrefs and are correctly ignored by the href regex.
    assert len(_heaven()) == 14


def test_heaven_hall_parameterization_and_venue_meta():
    s = LoftScraper("loft_heaven")
    assert s.source_id == "loft_heaven"
    assert s.hall["slug"] == "heaven"
    e = next(iter(_heaven().values()))
    assert e.venue_name == "LOFT HEAVEN"
    assert e.venue_area == "Shibuya"
    assert e.category == Category.MUSIC


def test_heaven_field_spot_checks():
    evs = _heaven()
    # Clean acoustic-live card (no icon/d_time noise in the title).
    e = evs["356753"]
    assert e.title_ja == \
        "【one K rew 会員限定】KAZUTO ARAKI ACOUSTIC LIVE Secret Night"
    assert e.start_date == "2026-07-20"
    assert (e.open_time, e.start_time) == ("18:00", "18:30")

    # Second card: title + date + OPEN/START all read from the listing.
    m = evs["360140"]
    assert "まちだガールズ・クワイア" in m.title_ja
    assert m.start_date == "2026-07-17"
    assert (m.open_time, m.start_time) == ("18:30", "19:00")


def test_heaven_detail_urls_are_absolute_https():
    for e in _heaven().values():
        assert e.source_url.startswith(
            "https://www.loft-prj.co.jp/schedule/heaven/")
    # HEAVEN detail links, like SHELTER, lack the second /schedule/ segment
    # that the main LOFT hall uses.
    assert all("/schedule/heaven/schedule/" not in e.source_url
               for e in _heaven().values())


def test_heaven_same_night_two_shows_kept_separately():
    # XANVALA matinee (15:30) and evening (19:00) on 2026-07-13 share a title
    # but have distinct detail URLs -> both survive the source_url dedupe.
    evs = _heaven()
    assert evs["356561"].start_date == evs["355258"].start_date == "2026-07-13"
    assert evs["356561"].open_time == "15:30"
    assert evs["355258"].open_time == "19:00"


def test_heaven_empty_html_returns_no_events():
    # Structural failure must be loud (found=0), never silent garbage.
    assert LoftScraper("loft_heaven").parse("<html></html>") == []


def test_heaven_nonconcert_rows_currently_pass_through_as_music():
    """KNOWN LIMITATION, documented for the integrator.

    The shared LoftScraper.parse() (deliberately not modified for this hall)
    tags every listing row Category.MUSIC. HEAVEN's calendar mixes in a few
    "Private party" private room rentals (e.g. id 360221) and reading-drama
    "朗読劇" nights (id 360234) that are not concerts. They flow through as
    MUSIC today; downstream review/genre tagging should filter them.
    """
    evs = _heaven()
    assert evs["360221"].title_ja.endswith("Private party")
    assert evs["360221"].category == Category.MUSIC     # not yet filtered
    assert "朗読劇" in evs["360234"].title_ja
    assert evs["360234"].category == Category.MUSIC
