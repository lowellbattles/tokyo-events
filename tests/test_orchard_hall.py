import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.orchard_hall import OrchardHallScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    # Absolute dates in the listing -> no year inference, so parse() needs no
    # pinned `today`; key on the detail id for lookups.
    return {e.source_url.rsplit("/", 1)[1]: e
            for e in OrchardHallScraper().parse(_load("orchard_hall_live.html"))}


# ----------------------------------------------------------------- listing
def test_parses_exact_orchard_count():
    # The listing is multi-venue (38 rows across all operating Bunkamura
    # halls); exactly 17 rows have 会場 == オーチャードホール. Sibling-venue
    # rows (Tokyu Theatre Orb, Cerulian Noh, Yokohama MM, ...) are dropped.
    evs = OrchardHallScraper().parse(_load("orchard_hall_live.html"))
    assert len(evs) == 17
    assert len({e.source_url for e in evs}) == 17   # dedupe by detail URL


def test_field_spotchecks():
    ev = _events()
    # Pop concert — plain title, single date.
    a = ev["5565"]
    assert a.title_ja == "今井美樹 40th Anniversary"
    assert a.start_date == "2026-08-02"
    assert a.end_date is None
    assert a.category == Category.MUSIC
    # Orchestra subscription — title carries half-width katakana + full-width
    # digits (東京ﾌｨﾙ 第１０３５回ｵｰﾁｬｰﾄﾞ) that MUST be NFKC-normalized.
    b = ev["5570"]
    assert b.title_ja == "東京フィル 第1035回オーチャード定期演奏会"
    assert b.start_date == "2026-07-26"


def test_multiday_range_uses_start_and_end():
    ev = _events()
    # 平原綾香 runs 10/11〜10/12 -> one event spanning the range.
    h = ev["5551"]
    assert h.start_date == "2026-10-11"
    assert h.end_date == "2026-10-12"
    assert h.category == Category.MUSIC


def test_absolute_urls_and_venue_meta():
    ev = _events()
    a = ev["5565"]
    assert a.source_url == \
        "https://my.bunkamura.co.jp/ticket/ProgramDetail/index/5565"
    for e in ev.values():
        assert e.source_url.startswith(
            "https://my.bunkamura.co.jp/ticket/ProgramDetail/index/")
        assert e.venue_name == "Bunkamura オーチャードホール"
        assert e.venue_area == "Shibuya"
        assert e.genres == []            # tagging happens at export


# ---------------------------------------------------------------- category
def test_ballet_rows_are_other_concerts_are_music():
    ev = _events()
    # Category policy: ballet -> OTHER. Both K-BALLET rows (multi-day runs).
    assert ev["5561"].category == Category.OTHER      # K-BALLET SELECTIONS
    assert ev["5676"].category == Category.OTHER      # 熊川哲也 クレオパトラ
    # Everything else in the Orchard window is an orchestral/recital/pop
    # concert -> MUSIC. Exactly 2 OTHER, 15 MUSIC.
    cats = [e.category for e in ev.values()]
    assert cats.count(Category.OTHER) == 2
    assert cats.count(Category.MUSIC) == 15


def test_empty_and_structureless_html_returns_nothing():
    # Loud structural failure: no detail-button rows -> no events.
    s = OrchardHallScraper()
    assert s.parse("<html></html>") == []
    assert s.parse("") == []
    # A row whose expected cells vanished must not be slotted by position.
    no_cells = (
        '<table><tr><td>whatever</td>'
        '<img id="btnDetailOrch_9" val="9"></tr></table>')
    assert s.parse(no_cells) == []


# ------------------------------------------------------------------ detail
def test_detail_fills_times_price_from_ticket_page():
    # Custom parse_detail: the ticketing page uses 開演/開場 + 席種 ¥ tiers,
    # not English OPEN/START. K-BALLET (5561) detail page: first 開演 13:00,
    # doors = start-30 = 12:30 ("開場は開演の30分前"), 席種 S/A/B/C = min ¥9,000.
    ev = _events()["5561"]
    OrchardHallScraper().parse_detail(_load("orchard_hall_detail.html"), ev)
    assert ev.start_time == "13:00"
    assert ev.open_time == "12:30"
    assert ev.price_min == 9000
    assert ev.is_free is False
    # Availability table still shows ◎○△ -> not a full sell-out.
    assert ev.is_sold_out is False
