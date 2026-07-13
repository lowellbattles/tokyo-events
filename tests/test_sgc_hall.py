"""Tests for the SGC HALL ARIAKE scraper (JSON feed + static detail pages).

Fixtures (captured 2026-07-13, UTF-8):
- sgc_hall_ariake_live.json : the venue's own /hall/json/hall-event.json — the
  full 133-record feed, so it carries past rows (to exercise the date filter)
  and reserved "<span class='today-none'></span>" placeholder slots.
- sgc_hall_ariake_detail.html : a real detail page (/hall/event/0057/,
  斉藤和義 KAZUYOSHI SAITO LIVE TOUR 2026).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.sgc_hall import SgcHallScraper

FIX = Path(__file__).parent / "fixtures"
TODAY = "2026-07-13"   # pin 'today' so the forward-window filter is deterministic


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    evs = SgcHallScraper().parse(_load("sgc_hall_ariake_live.json"), today=TODAY)
    return {e.source_url: e for e in evs}


# --------------------------------------------------------------- listing
def test_parses_exact_future_count():
    # 54 of the 133 feed rows have not finished as of the pinned today;
    # every past row and every "today-none" placeholder is dropped.
    assert len(_events()) == 54


def test_single_day_no_br_title_and_absolute_url():
    e = _events()["https://tdp.tv-asahi.co.jp/hall/event/0042/"]
    assert e.title_ja == (
        "いきものがかりの みなさん、こんにつあー!! 2026-2027 超全国あんぎゃー!!")
    assert e.subtitle is None            # no <br> -> no subtitle
    assert e.start_date == "2027-04-10"
    assert e.end_date is None            # single day -> no end_date
    assert e.category is Category.MUSIC
    assert e.venue_name == "SGCホール有明"
    assert e.venue_area == "Ariake"
    assert e.source_url.startswith("https://tdp.tv-asahi.co.jp/hall/event/")


def test_br_title_splits_into_act_and_subtitle():
    e = _events()["https://tdp.tv-asahi.co.jp/hall/event/0073/"]
    assert e.title_ja == "TWO DOOR CINEMA CLUB"
    assert e.subtitle == "15th ANNIVERSARY TOUR"
    assert e.start_date == "2026-11-30"
    assert e.end_date is None


def test_multiday_run_keeps_end_date():
    e = _events()["https://tdp.tv-asahi.co.jp/hall/event/0057/"]
    assert e.title_ja == "斉藤和義"
    assert e.subtitle == "KAZUYOSHI SAITO LIVE TOUR 2026"
    assert (e.start_date, e.end_date) == ("2026-12-05", "2026-12-06")


def test_placeholder_and_past_rows_dropped():
    ev = _events()
    # "<span class='today-none'></span>" reserved slot (id 0100, 2026-06-01)
    assert "https://tdp.tv-asahi.co.jp/hall/event/0100/" not in ev
    # a real but past event (id 0028, ended 2026-07-12, before pinned today)
    assert "https://tdp.tv-asahi.co.jp/hall/event/0028/" not in ev
    # every surviving event's run reaches on/after the pinned today
    for e in ev.values():
        assert (e.end_date or e.start_date) >= TODAY


def test_forward_window_bounds_events():
    # A tiny 1-month window drops everything that starts after August 1.
    evs = SgcHallScraper().parse(_load("sgc_hall_ariake_live.json"),
                                 today=TODAY, months_ahead=1)
    assert evs and all(e.start_date <= "2026-08-01" for e in evs)


def test_empty_or_garbage_json_returns_empty():
    s = SgcHallScraper()
    assert s.parse("", today=TODAY) == []            # loud, not a crash
    assert s.parse("<html></html>", today=TODAY) == []
    assert s.parse("[]", today=TODAY) == []
    assert s.parse('{"not":"a list"}', today=TODAY) == []


def test_nonmusic_row_classified_other():
    # SGC HALL's feed has no per-event type label (category is always
    # "sgchall"), so concert-vs-not rides on tu.is_nonmusic against the title.
    # Verify the wiring with a synthetic sports row — no invented keyword list.
    raw = ('[{"id":"9999","startDate":"2026-08-01","endDate":"",'
           '"category":"sgchall","title":"大相撲有明場所",'
           '"link":"/hall/event/9999/"}]')
    e = SgcHallScraper().parse(raw, today=TODAY)[0]
    assert e.category is Category.OTHER


# ---------------------------------------------------------------- detail
def test_detail_enrichment_times_and_price():
    ev = Event(source="sgc_hall_ariake",
               source_url="https://tdp.tv-asahi.co.jp/hall/event/0057/")
    SgcHallScraper().parse_detail(_load("sgc_hall_ariake_detail.html"), ev)
    # 公演時間: 12/5(土) 開場 16:30 / 開演 17:30 (first day's OPEN/START)
    assert (ev.open_time, ev.start_time) == ("16:30", "17:30")
    # チケット料金 is 指定席 ￥8,800(税込) — the age-note / on-sale date rows
    # must NOT be picked up as the price.
    assert ev.price_min == 8800


def test_detail_never_overwrites_listing_data():
    ev = Event(source="sgc_hall_ariake",
               source_url="https://tdp.tv-asahi.co.jp/hall/event/0057/",
               open_time="18:30", start_time="19:30", price_min=5000)
    SgcHallScraper().parse_detail(_load("sgc_hall_ariake_detail.html"), ev)
    assert (ev.open_time, ev.start_time, ev.price_min) == ("18:30", "19:30", 5000)
