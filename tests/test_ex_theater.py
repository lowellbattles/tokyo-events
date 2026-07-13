"""Tests for the EX THEATER ROPPONGI scraper (JSON feed + detail pages).

Fixtures:
- ex_theater_live.json : a faithful slice of the venue's own
  /schedule/json/schedule.json (records with start_date >= 2026-06-01, so it
  carries both past rows — to exercise the date filter — and the full future
  window as captured 2026-07-13).
- ex_theater_detail.html : a real detail page (schedule/2219, LINDBERG).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.ex_theater import ExTheaterScraper

FIX = Path(__file__).parent / "fixtures"
TODAY = "2026-07-13"   # pin 'today' so the forward-window filter is deterministic


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    evs = ExTheaterScraper().parse(_load("ex_theater_live.json"), today=TODAY)
    return {e.source_url: e for e in evs}


# --------------------------------------------------------------- listing
def test_parses_exact_future_count():
    # 27 real events remain after dropping past rows, maintenance holds,
    # and "Coming Soon" placeholders from the fixture slice.
    assert len(_events()) == 27


def test_single_day_fields_and_absolute_url():
    e = _events()["https://www.ex-theater.com/schedule/2219/index.html"]
    assert e.title_ja == "LINDBERG LIVE TOUR 2026 「Hello︕New Days」 振替公演"
    assert e.start_date == "2026-07-19"
    assert e.end_date is None            # single day -> no end_date
    assert e.category is Category.MUSIC
    assert e.venue_name == "EX THEATER ROPPONGI"
    assert e.venue_area == "Roppongi"
    assert e.source_url.startswith("https://www.ex-theater.com/schedule/")


def test_multiday_run_keeps_end_date():
    e = _events()["https://www.ex-theater.com/schedule/2250/index.html"]
    assert e.title_ja == "こじ研"
    assert (e.start_date, e.end_date) == ("2026-07-24", "2026-07-25")


def test_placeholder_and_coming_soon_and_past_rows_dropped():
    ev = _events()
    # maintenance=="有効" blank-title hold row (2026-07-26)
    assert "https://www.ex-theater.com/schedule/2273/index.html" not in ev
    # "Coming Soon" not-yet-announced slot (2026-07-21)
    assert "https://www.ex-theater.com/schedule/2242/index.html" not in ev
    # every surviving event is on/after the pinned today
    assert all(e.start_date >= TODAY for e in ev.values())


def test_forward_window_bounds_events():
    # A tiny 1-month window drops everything past August.
    evs = ExTheaterScraper().parse(_load("ex_theater_live.json"),
                                   today=TODAY, months_ahead=1)
    assert evs and all(e.start_date <= "2026-08-01" for e in evs)


def test_empty_or_garbage_json_returns_empty():
    s = ExTheaterScraper()
    assert s.parse("", today=TODAY) == []          # loud, not a crash
    assert s.parse("<html></html>", today=TODAY) == []
    assert s.parse("[]", today=TODAY) == []
    assert s.parse('{"not":"a list"}', today=TODAY) == []


def test_nonmusic_row_classified_other():
    # The live fixture happens to be all-music; verify the category wiring
    # (tu.is_nonmusic) with a synthetic sports row — no invented keyword list.
    raw = ('[{"start_date":"2026-08-01","end_date":"","maintenance":"",'
           '"title":"全日本フィギュアスケート選手権","cast":"",'
           '"url":"schedule/9999/index.html"}]')
    e = ExTheaterScraper().parse(raw, today=TODAY)[0]
    assert e.category is Category.OTHER


# ---------------------------------------------------------------- detail
def test_detail_enrichment_times_price_and_links():
    ev = Event(source="ex_theater",
               source_url="https://www.ex-theater.com/schedule/2219/index.html")
    ExTheaterScraper().parse_detail(_load("ex_theater_detail.html"), ev)
    # 公演時間: 開場 16:00 / 開演 17:00
    assert (ev.open_time, ev.start_time) == ("16:00", "17:00")
    # チケット料金 is ￥7,700 — NOT the separate ￥600 drink charge.
    assert ev.price_min == 7700
    providers = {t["provider"] for t in ev.ticket_links}
    assert {"pia", "lawson", "eplus"} <= providers


def test_detail_never_overwrites_listing_data():
    ev = Event(source="ex_theater",
               source_url="https://www.ex-theater.com/schedule/2219/index.html",
               open_time="18:30", start_time="19:30", price_min=5000)
    ExTheaterScraper().parse_detail(_load("ex_theater_detail.html"), ev)
    assert (ev.open_time, ev.start_time, ev.price_min) == ("18:30", "19:30", 5000)
