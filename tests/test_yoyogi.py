"""Tests for the Yoyogi National Gymnasium (第一体育館) scraper.

Fixtures are raw HTML saved from the official JAPAN SPORT COUNCIL site
(2026-07-13):
  yoyogi_gym1_live.html   — the single schedule page (event/tabid/59). The
    present-month table holds 9 date-rows (9 distinct eids); next month and
    month-after-next both render "イベントはありません。". The same table is
    also mirrored in a sidebar 月間予定表 copy — dedupe must collapse it.
  yoyogi_gym1_detail.html — one event detail page (eid=6004, RIIZE), with
    開場時間 / 開始時間 and a 特設サイト お問合わせ先 link.

Mixed sports/concert calendar: KAWAII LAB. + RIIZE are MUSIC; the karate
(カラテ) and wrestling (レスリング選手権) tournaments are Category.OTHER.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.yoyogi import YoyogiScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.source_url: e
            for e in YoyogiScraper("yoyogi_gym1").parse(
                _load("yoyogi_gym1_live.html"))}


def _by_eid(eid):
    evs = _events()
    for url, e in evs.items():
        if f"eid={eid}&" in url or url.endswith(f"eid={eid}"):
            return e
    raise KeyError(eid)


# ------------------------------------------------------------------- listing
def test_parses_every_present_month_day_deduping_sidebar_copy():
    # 9 distinct eids across the present-month table; the identical sidebar
    # 月間予定表 copy and the two empty future months add nothing.
    assert len(_events()) == 9


def test_first_kawaii_lab_row_fields():
    e = _by_eid(6002)
    assert e.title_ja == "KAWAII LAB. SESSION 2026 SUMMER"
    assert e.start_date == "2026-07-10"
    assert e.category == Category.MUSIC
    assert e.venue_name == "国立代々木競技場 第一体育館"
    assert e.venue_area == "Harajuku"


def test_riize_fanmeeting_row_fields():
    e = _by_eid(6004)
    assert e.title_ja == "RIIZE JAPAN FANMEETING 2026 RPG - RIIZE PLAYING GAME -"
    assert e.start_date == "2026-07-15"
    assert e.category == Category.MUSIC


def test_multiday_run_days_are_distinct_events_with_distinct_urls():
    # KAWAII LAB. runs Jul 10 (eid 6002) & Jul 11 (eid 6003) — the site gives
    # each day its own eid/detail page, so they are two separate events.
    d10, d11 = _by_eid(6002), _by_eid(6003)
    assert d10.source_url != d11.source_url
    assert d10.title_ja == d11.title_ja
    assert (d10.start_date, d11.start_date) == ("2026-07-10", "2026-07-11")


# ------------------------------------------------------------------- URLs
def test_source_urls_are_absolute_https_detail_links():
    for url in _events():
        assert url.startswith(
            "https://www.jpnsport.go.jp/yoyogi/Default.aspx?TabId=59&eid=")


# ------------------------------------------------------- category / policy
def test_karate_tournament_is_other():
    # カラテドリームフェスティバル2026 国際大会 — sports, not a concert.
    e = _by_eid(6018)
    assert "カラテ" in e.title_ja
    assert e.category == Category.OTHER


def test_wrestling_championship_is_other():
    # 令和8年度 第43回全国少年少女レスリング選手権大会 — sports.
    e = _by_eid(6020)
    assert "レスリング" in e.title_ja
    assert e.category == Category.OTHER


def test_category_split_matches_expected_music_vs_other():
    cats = [e.category for e in _events().values()]
    assert cats.count(Category.MUSIC) == 4      # KAWAII LAB x2 + RIIZE x2
    assert cats.count(Category.OTHER) == 5      # karate x2 + wrestling x3


# ------------------------------------------------------------------- detail
def test_parse_detail_fills_times_and_promoter_link():
    ev = Event(
        source="yoyogi_gym1",
        source_url=("https://www.jpnsport.go.jp/yoyogi/Default.aspx"
                    "?TabId=59&eid=6004&etype=1"),
        title_ja="RIIZE JAPAN FANMEETING 2026 RPG - RIIZE PLAYING GAME -",
        category=Category.MUSIC, start_date="2026-07-15",
    )
    out = YoyogiScraper("yoyogi_gym1").parse_detail(
        _load("yoyogi_gym1_detail.html"), ev)
    assert out.open_time == "17:00"          # 開場時間
    assert out.start_time == "18:00"         # 開始時間
    assert out.ticket_url == "https://riize-fanmeeting2026.jp/"  # 特設サイト


# ------------------------------------------------------------- loud failure
def test_empty_html_yields_no_events():
    assert YoyogiScraper("yoyogi_gym1").parse("<html></html>") == []


def test_unknown_hall_raises():
    try:
        YoyogiScraper("yoyogi_gym_bogus")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown hall")
