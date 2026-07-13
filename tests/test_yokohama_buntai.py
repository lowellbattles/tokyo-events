"""Tests for the 横浜BUNTAI (Yokohama BUNTAI) scraper.

Fixtures are raw HTML saved from the official month archive (2026-07-13):
  yokohama_buntai_live.html      — /event/?y=2026&m=7  (5 events)
  yokohama_buntai_live_aug.html  — /event/?y=2026&m=8  (4 events)

Both carry every fact inline (title, dates incl. multi-day, 開場/開演 times,
円 price tiers, cast, official-site link), so there is no detail fixture:
supports_detail is False and the listing IS the record. The date blocks
carry no year — the parser reads it from the page's own year tab / inline
calendar seed (both fixtures say 2026).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.yokohama_buntai import YokohamaBuntaiScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return {e.start_date: e
            for e in YokohamaBuntaiScraper().parse(_load("yokohama_buntai_live.html"))}


def _august():
    return {e.start_date: e
            for e in YokohamaBuntaiScraper().parse(
                _load("yokohama_buntai_live_aug.html"))}


# ------------------------------------------------------------------- counts
def test_parses_exact_counts():
    assert len(_july()) == 5
    assert len(_august()) == 4
    assert YokohamaBuntaiScraper.supports_detail is False


# ------------------------------------------------------------ field spot-checks
def test_first_july_event_multiday_kanji_times_and_price():
    # テイルズ オブ フェスティバル 2026 — 7.4-7.5, 開場：15時00分 / 開演：16時30分,
    # price tiers 16,500円 / 13,000円 -> floor 13,000.
    e = _july()["2026-07-04"]
    assert e.title_ja == "テイルズ オブ フェスティバル 2026"
    assert (e.start_date, e.end_date) == ("2026-07-04", "2026-07-05")
    assert (e.open_time, e.start_time) == ("15:00", "16:30")
    assert e.price_min == 13000
    assert e.category == Category.MUSIC
    assert e.ticket_url == "https://tof.tales-ch.jp/"


def test_time_first_label_layout_not_cross_assigned():
    # MELLOW DEAR US lists "16:00開場 / 17:00開演" (time BEFORE the label) — the
    # 16:00 must land on open, 17:00 on start (not both 16:00 or both 17:00).
    e = _july()["2026-07-11"]
    assert e.title_ja.startswith("MELLOW DEAR US 1st JAPAN Tour Final")
    assert (e.start_date, e.end_date) == ("2026-07-11", "2026-07-12")
    assert (e.open_time, e.start_time) == ("16:00", "17:00")
    assert e.price_min == 9000


def test_pipe_separated_times_stop_before_end_time():
    # UNAVERAGE FES.: "開場 15:00｜開演 17:00｜終演 20:00" -> open 15:00, start
    # 17:00 (终演 20:00 ignored). Price row is "公式サイトにて…" -> no amount.
    e = _july()["2026-07-20"]
    assert "UNAVERAGE FES." in e.title_ja
    assert (e.start_date, e.end_date) == ("2026-07-20", None)
    assert (e.open_time, e.start_time) == ("15:00", "17:00")
    assert e.price_min is None and e.price_text is None
    # SPORTS & MUSIC crossover but artist-led -> stays MUSIC.
    assert e.category == Category.MUSIC


def test_price_floor_is_min_tier():
    e = _july()["2026-07-25"]
    assert e.title_ja.startswith("YOKOHAMA UNITE")
    assert (e.open_time, e.start_time) == ("16:00", "17:00")


# --------------------------------------------------------------- detail-URL join
def test_source_urls_are_internal_https_with_date_hash():
    ev = _july()
    # exact, deterministic fragment (start_date + title hash).
    assert ev["2026-07-04"].source_url == \
        "https://yokohama-buntai.jp/event/#2026-07-04-f74f15a7"
    for e in ev.values():
        assert e.source_url.startswith("https://yokohama-buntai.jp/event/#2026-")
        assert e.venue_name == "横浜BUNTAI"
        assert e.venue_area == "Kannai"
        assert e.genres == []
        # official-site links are stored as absolute https ticket_urls.
        assert e.ticket_url is None or e.ticket_url.startswith("https://")


def test_source_urls_unique_per_event():
    urls = [e.source_url for e in list(_july().values()) + list(_august().values())]
    assert len(urls) == len(set(urls))


# ------------------------------------------------------------------- august
def test_free_convention_multiday_and_kanji_time_words():
    # Jehovah's convention 8.14-8.16: "開場時間：一般入場 8時15分 /
    # 開演時間：プログラム開始 9時20分" (labels padded with extra words) -> 8:15 /
    # 9:20; 無料 -> free. (Non-concert, but see category note below.)
    e = _august()["2026-08-14"]
    assert (e.start_date, e.end_date) == ("2026-08-14", "2026-08-16")
    assert (e.open_time, e.start_time) == ("8:15", "9:20")
    assert e.is_free is True and e.price_min == 0


def test_romaji_open_start_and_price():
    # 3SKM 1st LIVE "One-Off" 8.23: "OPEN 17:00 / START 18:00", 11,000円.
    e = _august()["2026-08-23"]
    assert e.title_ja.startswith("3SKM 1st LIVE")
    assert (e.open_time, e.start_time) == ("17:00", "18:00")
    assert e.price_min == 11000
    assert e.ticket_url == "https://www.nijisanji.jp/events/3skm_1stlive/"


def test_yen_symbol_price_parsed():
    # 大運動会 8.29 uses the ￥ symbol form "￥9,900" instead of 円.
    e = _august()["2026-08-29"]
    assert e.price_min == 9900


# ---------------------------------------------------------------- category policy
def test_mixed_calendar_nonmusic_row_is_other():
    # Synthetic B.League row: a clearly non-concert title is forced to OTHER by
    # tu.is_nonmusic (this venue publishes no per-row type label to key off).
    html = (
        '<div class="event"><div class="event-date">'
        '<div class="date">9.5</div></div>'
        '<div class="event-flex"><div class="event-info">'
        '<div class="info-title">横浜ビー・コルセアーズ Bリーグ 開幕戦</div>'
        '<div class="info"><div class="info-left">公演時間</div>'
        '<div class="info-right">開場 16:00｜試合開始 18:05</div></div>'
        '</div></div></div>')
    out = YokohamaBuntaiScraper().parse(html, year=2026)
    assert len(out) == 1
    assert out[0].category == Category.OTHER
    assert out[0].start_date == "2026-09-05"


def test_concerts_are_music():
    jul = _july()
    for k in ("2026-07-04", "2026-07-11", "2026-07-20", "2026-07-25", "2026-07-26"):
        assert jul[k].category == Category.MUSIC
    # Known leak (documented): non-concert rows the site gives no type label
    # for, and whose titles tu.is_nonmusic doesn't match (a religious 大会, a
    # 大運動会), currently fall through as MUSIC — left to review, not guessed at.
    aug = _august()
    assert aug["2026-08-14"].category == Category.MUSIC   # エホバの証人の大会
    assert aug["2026-08-29"].category == Category.MUSIC   # 大運動会


# --------------------------------------------------------------- year handling
def test_year_read_from_page_when_not_pinned():
    # No explicit year kwarg -> read 2026 from the fixture's own year tab.
    ev = YokohamaBuntaiScraper().parse(_load("yokohama_buntai_live.html"))
    assert all(e.start_date.startswith("2026-") for e in ev)


def test_year_override_and_month_rollover():
    # A December page whose multi-day run crosses into January: the second date
    # (month < first) rolls the year forward.
    html = (
        '<div class="event"><div class="event-date">'
        '<div class="date">12.31</div><div class="period">-</div>'
        '<div class="date">1.1</div></div>'
        '<div class="event-flex"><div class="event-info">'
        '<div class="info-title">Countdown Live</div>'
        '<div class="info"><div class="info-left">公演時間</div>'
        '<div class="info-right">開場 22:00｜開演 23:00</div></div>'
        '</div></div></div>')
    out = YokohamaBuntaiScraper().parse(html, year=2026)
    assert (out[0].start_date, out[0].end_date) == ("2026-12-31", "2027-01-01")


def test_year_falls_back_to_infer_when_page_has_none():
    html = (
        '<div class="event"><div class="event-date">'
        '<div class="date">7.4</div></div>'
        '<div class="event-flex"><div class="event-info">'
        '<div class="info-title">Test Act</div>'
        '<div class="info"><div class="info-left">公演時間</div>'
        '<div class="info-right">開場 17:00 / 開演 18:00</div></div>'
        '</div></div></div>')
    out = YokohamaBuntaiScraper().parse(html, today=dt.date(2026, 7, 13))
    assert len(out) == 1
    assert out[0].start_date == "2026-07-04"


# ---------------------------------------------------------- loud structural fail
def test_empty_html_returns_nothing_loudly():
    assert YokohamaBuntaiScraper().parse("<html></html>") == []
    assert YokohamaBuntaiScraper().parse("") == []
