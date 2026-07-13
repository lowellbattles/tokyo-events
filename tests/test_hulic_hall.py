"""Tests for the ヒューリックホール東京 (Hulic Hall Tokyo) scraper.

Fixtures are raw HTML saved from hulic-theater.com (2026-07-13):
  hulic_hall_live.html      — July 2026 month page (3 events)
  hulic_hall_live_aug.html  — August 2026 month page (1 event, 円 pricing,
                              plus an <li class="official"> artist link that
                              must NOT be mistaken for a ticket vendor)

The venue publishes no per-event detail pages (events link out to external
ticket vendors), so every fact is parsed from the listing and supports_detail
is False. All rows share the month page, so each event's source_url gets a
unique #YYYY-MM-DD fragment.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.hulic_hall import HulicHallScraper

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 7, 13)   # pin 'today' so any inference is deterministic


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return {e.source_url: e
            for e in HulicHallScraper().parse(_load("hulic_hall_live.html"),
                                              today=TODAY)}


# ------------------------------------------------------------------- listing
def test_july_parses_all_three_events():
    # Low volume (~3/month) is normal for this hall; the July fixture has
    # exactly 3 dt/dd pairs in <dl id="scheduleline">.
    assert len(_july()) == 3


def test_listing_fields_title_date_times_price():
    evs = _july()
    mm = evs["https://hulic-theater.com/entertainment/schedules/?d=202607#2026-07-10"]
    assert mm.title_ja == "Maverick Mom ONEMAN TOUR 「TRAVESSIA」"
    assert mm.start_date == "2026-07-10"          # dt spans 07/10 + header 2026
    assert (mm.open_time, mm.start_time) == ("18:15", "19:00")
    assert mm.price_min == 4000                   # 全席指定 ¥4,000
    assert mm.category == Category.MUSIC
    assert mm.venue_name == "ヒューリックホール東京"
    assert mm.venue_area == "Yurakucho"

    # "Open15:30"-style (spacing varies) still parses on the Aug fixture.
    shachi = evs["https://hulic-theater.com/entertainment/schedules/?d=202607#2026-07-24"]
    assert shachi.title_ja == "Mr.シャチホコのSHOWTIME4"
    assert shachi.start_date == "2026-07-24"
    assert shachi.price_min == 3500


def test_lineup_slash_split_and_min_price_across_tiers():
    ev = _july()["https://hulic-theater.com/entertainment/schedules/?d=202607#2026-07-11"]
    assert ev.title_ja == "THE UNITY SUPER VOICES"
    assert ev.lineup == ["パク・ユチョン", "森崎ウィン", "ペルピンズ",
                         "THE YARA（屋良朝幸）"]
    # Tiers ¥16,000 / ¥14,000 / ¥12,000 / ¥3,000(child) -> floor 3,000.
    assert ev.price_min == 3000
    assert "¥16,000" in ev.price_text


def test_ticket_link_captured_and_malformed_href_rejected():
    evs = _july()
    # DISK GARAGE info link is a real https URL -> captured.
    mm = evs["https://hulic-theater.com/entertainment/schedules/?d=202607#2026-07-10"]
    assert mm.ticket_url == "https://info.diskgarage.com/"
    assert mm.ticket_links == [{"provider": "diskgarage",
                                "url": "https://info.diskgarage.com/",
                                "code": None}]
    # July 11's info href is the venue's malformed "http://info@jpma-jazz.or.jp"
    # (contains '@') -> rejected, no ticket link invented.
    voices = evs["https://hulic-theater.com/entertainment/schedules/?d=202607#2026-07-11"]
    assert voices.ticket_url is None
    assert voices.ticket_links == []


def test_detail_urls_are_absolute_https_with_month_fragment():
    evs = _july()
    for url, e in evs.items():
        assert url.startswith("https://hulic-theater.com/entertainment/schedules/?d=202607#")
        assert url.endswith(e.start_date)


# -------------------------------------------------------------- august (円)
def test_august_yen_kanji_price_and_official_link_not_a_ticket():
    evs = {e.source_url: e
           for e in HulicHallScraper().parse(
               _load("hulic_hall_live_aug.html"), today=TODAY)}
    assert len(evs) == 1
    ev = next(iter(evs.values()))
    assert ev.title_ja.startswith("THE SUPER FRUIT")
    assert ev.start_date == "2026-08-22"
    assert (ev.open_time, ev.start_time) == ("15:30", "16:00")  # "Open15:30"
    assert ev.price_min == 6800                # "指定席 6,800円" (円, not ¥)
    assert ev.lineup == ["THE SUPER FRUIT"]
    # <li class="official"> points at supafuru.jp — an artist site, NOT the
    # ticket vendor (which lives in <li class="info">, here URL-less).
    assert ev.ticket_url is None
    assert ev.ticket_links == []
    assert ev.source_url == \
        "https://hulic-theater.com/entertainment/schedules/?d=202608#2026-08-22"


# ---------------------------------------------------------------- policies
def test_empty_html_returns_no_events_loud_failure():
    assert HulicHallScraper().parse("<html></html>") == []
    assert HulicHallScraper().parse("") == []


def test_nonconcert_row_categorized_other():
    # The hall also hosts formal/business events. A row the shared
    # NONMUSIC_RE recognises (株主総会 / shareholders' meeting) must land as
    # Category.OTHER, not MUSIC.
    html = (
        '<h3 class="month"><span>September / 09月　</span><span>2026</span></h3>'
        '<dl id="scheduleline">'
        '<dt><div class="weekday"><span class="month">09</span>'
        '<span class="day">15</span><span class="week">Tue</span></div></dt>'
        '<dd><div><h4>ヒューリック株式会社 定時株主総会</h4>'
        '<ul class="schedule"><li class="open">Open10:00｜Start10:30</li>'
        '</ul></div></dd></dl>')
    evs = HulicHallScraper().parse(html, today=TODAY)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
    assert evs[0].start_date == "2026-09-15"


def test_same_day_events_get_distinct_fragments():
    # Two shows on one day (matinee/evening) share the month page; the parser
    # must keep both by suffixing the fragment rather than overwriting.
    html = (
        '<h3 class="month"><span>October / 10月　</span><span>2026</span></h3>'
        '<dl id="scheduleline">'
        '<dt><div class="weekday"><span class="month">10</span>'
        '<span class="day">03</span><span class="week">Sat</span></div></dt>'
        '<dd><div><h4>昼公演</h4><ul class="schedule">'
        '<li class="open">Open12:30｜Start13:00</li></ul></div></dd>'
        '<dt><div class="weekday"><span class="month">10</span>'
        '<span class="day">03</span><span class="week">Sat</span></div></dt>'
        '<dd><div><h4>夜公演</h4><ul class="schedule">'
        '<li class="open">Open17:30｜Start18:00</li></ul></div></dd></dl>')
    evs = HulicHallScraper().parse(html, today=TODAY)
    assert len(evs) == 2
    urls = {e.source_url for e in evs}
    assert urls == {
        "https://hulic-theater.com/entertainment/schedules/?d=202610#2026-10-03",
        "https://hulic-theater.com/entertainment/schedules/?d=202610#2026-10-03-2",
    }


def test_supports_detail_is_false():
    assert HulicHallScraper.supports_detail is False
