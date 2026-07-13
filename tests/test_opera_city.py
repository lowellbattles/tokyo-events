"""Tests for the 東京オペラシティ (Tokyo Opera City) Concert/Recital Hall scraper.

Fixture: opera_city_live.html — the July 2026 listing fragment saved verbatim
from the venue's own internal endpoint
  /contents/performance?lang=ja&year=2026&month=7&presented_only=0&calendar=0
(2026-07-13). It is an HTML fragment (no page chrome); the visible
/concert/calendar/ page AJAX-loads exactly this.

The fragment holds 42 real performances (each carrying a
/concert/calendar/detail.php?id=N link) intermixed with 18 non-event rows
(保守点検 maintenance / リハーサル rehearsal / 公演予定 private bookings) that
have no detail link and must be skipped. Everything is parsed from the
listing, so supports_detail is False.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.opera_city import OperaCityScraper

FIX = Path(__file__).parent / "fixtures"
MONTH = dt.date(2026, 7, 1)     # pin the page month so the year is deterministic
TODAY = dt.date(2026, 7, 13)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return {e.source_url: e
            for e in OperaCityScraper().parse(_load("opera_city_live.html"),
                                              month=MONTH, today=TODAY)}


# --------------------------------------------------------------- event count
def test_july_parses_exactly_42_performances():
    # 42 cards carry a /concert/calendar/detail.php?id= link; the 18
    # maintenance/rehearsal/private rows (no link) are skipped.
    assert len(_july()) == 42


# --------------------------------------------------------------- field checks
def test_concert_hall_event_fields():
    ev = _july()["https://www.operacity.jp/concert/calendar/detail.php?id=17717"]
    assert ev.title_ja == "樫本大進×小菅 優×クラウディオ・ボルケス トリオ 2026"
    assert ev.start_date == "2026-07-02"          # date cell 7/2 + page year
    assert ev.start_time == "19:00"               # single 開演 time
    assert ev.category == Category.MUSIC
    assert ev.venue_name == "東京オペラシティ コンサートホール"
    assert ev.venue_area == "Hatsudai"
    # 「お問い合わせ」 outbound promoter link is kept as the ticket link.
    assert ev.ticket_url == \
        "https://www.kajimotomusic.com/concerts/kashimoto-kosuge-bohorquez-2026/"
    assert ev.ticket_links == [{
        "provider": "kajimotomusic",
        "url": "https://www.kajimotomusic.com/concerts/kashimoto-kosuge-bohorquez-2026/",
        "code": None}]


def test_recital_hall_room_and_multiline_title():
    # 7/1 18:30 リサイタルホール — title spans a <br> and must join with a space.
    ev = _july()["https://www.operacity.jp/concert/calendar/detail.php?id=17731"]
    assert ev.venue_name == "東京オペラシティ リサイタルホール"
    assert ev.start_date == "2026-07-01"
    assert ev.start_time == "18:30"
    assert ev.title_ja == (
        "第53回フルートデビューリサイタル "
        "各音楽大学より推薦された若きフルーティストたちによる")


def test_promoter_link_on_foreign_detail_php_not_confused_with_venue_link():
    # 東京シティ・フィル (id=17727) lists its OWN cityphil.jp/concert/detail.php
    # promoter link. That contains "detail.php?id=" but not the venue's
    # "/concert/calendar/" path, so it is kept as the ticket link — and does
    # NOT spawn a second (id=740) event.
    evs = _july()
    ev = evs["https://www.operacity.jp/concert/calendar/detail.php?id=17727"]
    assert ev.ticket_url == "https://www.cityphil.jp/concert/detail.php?id=740&y=2026&m=7"
    assert ev.ticket_links[0]["provider"] == "cityphil"
    assert "https://www.operacity.jp/concert/calendar/detail.php?id=740" not in evs


def test_text_only_contact_yields_no_invented_ticket_link():
    # id=17674 (コンポージアム, run by the venue) has a text-only contact
    # "東京オペラシティチケットセンター" with no URL -> no ticket link invented.
    ev = _july()["https://www.operacity.jp/concert/calendar/detail.php?id=17674"]
    assert ev.ticket_url is None
    assert ev.ticket_links == []


# --------------------------------------------------------------- URL join
def test_detail_urls_are_absolute_https_on_venue_domain():
    for url, e in _july().items():
        assert url.startswith(
            "https://www.operacity.jp/concert/calendar/detail.php?id=")
        assert e.source_url == url


# --------------------------------------------------------------- policies
def test_nonevent_rows_are_skipped():
    # None of the venue-operations rows (no detail link) should appear.
    titles = {e.title_ja for e in _july().values()}
    for junk in ("保守点検", "リハーサル", "公演予定", "公演予定（関係者のみ）"):
        assert junk not in titles


def test_empty_html_returns_no_events_loud_failure():
    assert OperaCityScraper().parse("<html></html>") == []
    assert OperaCityScraper().parse("") == []


def test_supports_detail_is_false():
    assert OperaCityScraper.supports_detail is False


def test_all_events_are_music_category():
    # This is a pure classical concert hall (no sports/expo mixing); the
    # only OTHER rows would be tu.is_nonmusic matches, of which July has none.
    assert all(e.category == Category.MUSIC for e in _july().values())
