"""Tests for the 幕張メッセ (Makuhari Messe) scraper.

Fixtures saved live 2026-07-13:
  makuhari_messe_live.html   — /event/?c=2 (music-category feed). The `c=2`
    server-side filter returns EVERY upcoming 音楽イベント on one page; this
    capture has 5 concert cards (UNISON SQUARE GARDEN, GLAY, Vaundy, 超PMAM,
    氣志團万博). Trade shows / expos live under other c-values and are never
    fetched, so they never reach the parser.
  makuhari_messe_detail.html — /event/detail/8865 (UNISON SQUARE GARDEN),
    carrying the time / price / promoter-URL facts the detail pass fills.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.makuhari_messe import MakuhariMesseScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    evs = MakuhariMesseScraper().parse(_load("makuhari_messe_live.html"))
    return {e.source_url.rsplit("/", 1)[-1]: e for e in evs}


# --------------------------------------------------------------------- count
def test_parses_exact_card_count():
    # 5 music cards on the c=2 feed: 8865, 8835, 8836, 8849, 8786.
    assert len(_events()) == 5
    assert MakuhariMesseScraper.supports_detail is True


# --------------------------------------------------------------- field checks
def test_single_day_concert_fields():
    e = _events()["8865"]
    assert e.title_ja == "UNISON SQUARE GARDEN「Sentimental Period」"
    assert e.start_date == "2026-07-15"
    assert e.end_date is None
    assert e.category == Category.MUSIC
    assert e.genres == []


def test_multiday_range_spans_both_dates():
    # GLAY runs 2026.07.31(金) 〜 2026.08.01(土) -> one event, start..end.
    g = _events()["8835"]
    assert g.title_ja.startswith("HAPPY SWING 30th Anniversary GLAY SPECIAL LIVE")
    assert (g.start_date, g.end_date) == ("2026-07-31", "2026-08-01")

    # Vaundy 2026.09.05(土) 〜 2026.09.06(日); &quot; entities decoded.
    v = _events()["8836"]
    assert v.title_ja == 'Vaundy ASIA ARENA TOUR 2026 "HORO"'
    assert (v.start_date, v.end_date) == ("2026-09-05", "2026-09-06")


# ------------------------------------------------------------- detail-URL join
def test_source_urls_are_absolute_internal_detail_pages():
    evs = _events()
    assert evs["8865"].source_url == "https://www.m-messe.co.jp/event/detail/8865"
    for e in evs.values():
        assert e.source_url.startswith("https://www.m-messe.co.jp/event/detail/")
        assert e.venue_name == "幕張メッセ"
        assert e.venue_area == "Makuhari"


# ------------------------------------------------------------- detail pass
def test_detail_pass_fills_time_price_and_promoter_url():
    e = _events()["8865"]
    # Pre-detail the listing has none of these.
    assert (e.start_time, e.price_min, e.ticket_url) == (None, None, None)
    MakuhariMesseScraper().parse_detail(_load("makuhari_messe_detail.html"), e)
    # dl.time gives a single "18:30～" (start only, no 開場).
    assert e.open_time is None
    assert e.start_time == "18:30"
    # Price "オールスタンディング(ブロック指定)\10,000" — backslash yen glyph.
    assert e.price_min == 10000
    assert e.is_free is False
    assert "オールスタンディング" in e.price_text
    # dl.url points at the promoter's own site, stored as ticket_url.
    assert e.ticket_url == "https://vintage-rock.com/"
    assert "m-messe.co.jp" not in e.ticket_url


# ------------------------------------------------------- category policy net
def test_non_music_category_label_forces_other():
    # A row whose OWN category tag is not 音楽イベント -> OTHER (guards against
    # the feed ever returning a mixed page).
    html = ('<li class="eventInr"><a href="/event/detail/999">'
            '<div class="category"><i></i>展示会・見本市</div>'
            '<div class="date">2026.08.10(月)</div>'
            '<div class="eventTit">Some Trade Show</div></a></li>')
    out = MakuhariMesseScraper().parse(html)
    assert len(out) == 1
    assert out[0].category == Category.OTHER


def test_nonmusic_title_under_music_label_forced_other():
    # Music label but a clearly non-concert title -> OTHER via tu.is_nonmusic.
    html = ('<li class="eventInr"><a href="/event/detail/998">'
            '<div class="category"><i></i>音楽イベント</div>'
            '<div class="date">2026.08.11(火)</div>'
            '<div class="eventTit">フィギュアスケート グランプリ</div></a></li>')
    out = MakuhariMesseScraper().parse(html)
    assert len(out) == 1
    assert out[0].category == Category.OTHER


# ---------------------------------------------------------- loud structural fail
def test_empty_html_returns_nothing_loudly():
    assert MakuhariMesseScraper().parse("<html></html>") == []
    assert MakuhariMesseScraper().parse("") == []
