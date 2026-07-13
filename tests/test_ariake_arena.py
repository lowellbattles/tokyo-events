import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.ariake_arena import AriakeArenaScraper

FIX = Path(__file__).parent / "fixtures"
# The card date blocks carry no year; the parser reads it from the page's own
# tab menu (months 7-11 all map to 2026 in these fixtures).


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = AriakeArenaScraper().parse(_load("ariake_arena_live.html"))
    return {e.source_url.rsplit("#", 1)[-1]: e for e in evs}


def _august():
    evs = AriakeArenaScraper().parse(_load("ariake_arena_live_aug.html"))
    return {e.source_url.rsplit("#", 1)[-1]: e for e in evs}


# ----------------------------------------------------------------- counts
def test_parses_exact_counts():
    # July page: detail- 313, 312, 310, 314, 326, 333  -> 6 cards.
    assert len(_july()) == 6
    # August page: 317, 328, 332, 335, 336, 337, 320, 316, 321 -> 9 cards.
    assert len(_august()) == 9


# ------------------------------------------------------------ field spot-checks
def test_field_spotchecks_concerts():
    ev = _july()
    # 原因は自分にある。 — 7.4-7.5, 開場 17:00 / 開演 18:00, 10,500円, subtitle.
    g = ev["detail-313"]
    assert g.title_ja == "原因は自分にある。"
    assert g.subtitle == "ARENA TOUR 2026 仮ノ現"
    assert (g.start_date, g.end_date) == ("2026-07-04", "2026-07-05")
    assert (g.open_time, g.start_time) == ("17:00", "18:00")
    assert g.price_min == 10500
    assert g.category == Category.MUSIC
    assert g.ticket_url == "https://genjibu.jp/"

    # THE YELLOW MONKEY — single day 7.7, 開場 18:00／開演 19:00, ¥10,000.
    y = ev["detail-312"]
    assert y.title_ja == "THE YELLOW MONKEY"
    assert (y.start_date, y.end_date) == ("2026-07-07", None)
    assert (y.open_time, y.start_time) == ("18:00", "19:00")
    assert y.price_min == 10000
    assert y.ticket_url == "http://theyellowmonkeysuper.jp/"

    # ACEes — three discrete days 7.10/7.11/7.12; ①開演 12:30 (no 開場);
    # price is "公式サイトをご覧ください" -> no amount.
    a = ev["detail-310"]
    assert (a.start_date, a.end_date) == ("2026-07-10", "2026-07-12")
    assert (a.open_time, a.start_time) == (None, "12:30")
    assert a.price_min is None and a.price_text is None


def test_price_min_is_headline_not_component():
    # FANTASTICS lists tiers whose breakdowns contain smaller ¥5,500 add-on
    # amounts; price_min must be the ¥12,100 headline, not ¥5,500.
    f = _july()["detail-326"]
    assert f.title_ja == "FANTASTICS"
    assert f.price_min == 12100
    assert (f.open_time, f.start_time) == ("15:00", "16:00")

    # Roselia (August) — min of three headline tiers is ¥9,900, not ¥22,000.
    r = _august()["detail-321"]
    assert r.title_ja == "Roselia"
    assert (r.start_date, r.end_date) == ("2026-08-29", "2026-08-30")
    assert (r.open_time, r.start_time) == ("16:30", "18:00")
    assert r.price_min == 9900


# ------------------------------------------------------------- date range shape
def test_range_dashed_span_spans_full_range():
    # ディズニー・オン・アイス: "7.17 FRI -" / "7.20 MON" -> 4-day range.
    d = _july()["detail-314"]
    assert (d.start_date, d.end_date) == ("2026-07-17", "2026-07-20")


# --------------------------------------------------------------- detail-URL join
def test_absolute_source_urls_and_venue_meta():
    ev = _july()
    assert ev["detail-313"].source_url == \
        "https://ariake-arena.tokyo/event/#detail-313"
    for e in ev.values():
        assert e.source_url.startswith("https://ariake-arena.tokyo/event/#detail-")
        assert e.venue_name == "有明アリーナ（TOKYO ARIAKE ARENA）"
        assert e.venue_area == "Ariake"
        assert e.genres == []


# ---------------------------------------------------------------- category policy
def test_category_policy_mixed_calendar():
    jul = _july()
    aug = _august()
    # Concerts / idol / anime-music -> MUSIC.
    for k in ("detail-313", "detail-312", "detail-310", "detail-326"):
        assert jul[k].category == Category.MUSIC
    for k in ("detail-317", "detail-332", "detail-320", "detail-321"):
        assert aug[k].category == Category.MUSIC
    # Non-concert rows -> OTHER.
    assert jul["detail-314"].category == Category.OTHER   # ディズニー・オン・アイス
    assert jul["detail-333"].category == Category.OTHER   # スポーツフェス (無料)
    assert aug["detail-328"].category == Category.OTHER   # ハンドボール
    assert aug["detail-316"].category == Category.OTHER   # バレーボール
    # Basketball national-team games: caught by 試合 / TIPOFF, not is_nonmusic.
    for k in ("detail-335", "detail-336", "detail-337"):
        assert aug[k].category == Category.OTHER


def test_free_sports_fest_is_free_and_other():
    fest = _july()["detail-333"]
    assert fest.is_free is True
    assert fest.price_min == 0
    assert fest.category == Category.OTHER


def test_basketball_tipoff_time_captured():
    # 三井不動産カップ has no 開演; the start_time falls back to TIPOFF 19:00.
    g = _august()["detail-335"]
    assert g.start_time == "19:00"
    assert g.open_time is None


# --------------------------------------------------------------- year handling
def test_year_read_from_page_tab_menu():
    # A synthetic page whose tab menu maps month 7 -> 2099; the card date has
    # no year, so start_date must follow the tab menu, not a hardcoded year.
    html = (
        '<div class="event_tab_menu"><ul><li><a>'
        '<span class="year">2099</span><span class="month_number">7</span>'
        '</a></li></ul></div>'
        '<ul class="event_detail_list" id="content07">'
        '<li id="detail-1">'
        '<div class="event_day"><p><span>7.4 SAT</span></p></div>'
        '<div class="event_name"><p>Test Act</p></div>'
        '<table class="detail_table"><tbody>'
        '<tr><th>公演時間</th><td>開場 17:00 / 開演 18:00</td></tr>'
        '</tbody></table></li></ul>'
    )
    out = AriakeArenaScraper().parse(html)
    assert len(out) == 1
    assert out[0].start_date == "2099-07-04"


def test_year_falls_back_to_infer_when_no_tab_menu():
    # No tab menu -> infer_year from a pinned "today".
    html = (
        '<ul class="event_detail_list" id="content07">'
        '<li id="detail-1">'
        '<div class="event_day"><p><span>7.4 SAT</span></p></div>'
        '<div class="event_name"><p>Test Act</p></div>'
        '<table class="detail_table"><tbody>'
        '<tr><th>公演時間</th><td>開場 17:00 / 開演 18:00</td></tr>'
        '</tbody></table></li></ul>'
    )
    out = AriakeArenaScraper().parse(html, today=dt.date(2026, 7, 13))
    assert len(out) == 1
    assert out[0].start_date == "2026-07-04"


# ---------------------------------------------------------- loud structural fail
def test_empty_html_returns_nothing_loudly():
    assert AriakeArenaScraper().parse("<html></html>") == []
    assert AriakeArenaScraper().parse("") == []
