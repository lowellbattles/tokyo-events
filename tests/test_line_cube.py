import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.line_cube import LineCubeShibuyaScraper

FIX = Path(__file__).parent / "fixtures"
# The listing fixtures are the eventYYYYMM month archives; the card date block
# carries no year, so pin the page month exactly as scrape() would.
JULY = dt.date(2026, 7, 1)
AUGUST = dt.date(2026, 8, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = LineCubeShibuyaScraper().parse(_load("line_cube_shibuya_live.html"),
                                         month=JULY)
    return {e.source_url.rsplit("/", 1)[-1]: e for e in evs}


def _august():
    evs = LineCubeShibuyaScraper().parse(_load("line_cube_shibuya_live_aug.html"),
                                         month=AUGUST)
    return {e.source_url.rsplit("/", 1)[-1]: e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_counts():
    # 24 <article> cards in July, 16 in August; one event per card.
    assert len(_july()) == 24
    assert len(_august()) == 16


def test_field_spotchecks_from_fixture():
    ev = _july()
    # Reol Oneman Live 2026, 7/10, 開場時間 17:30 / 開演時間 18:30
    r = ev["17256"]
    assert r.title_ja == "Reol Oneman Live 2026 「美辞学」"
    assert (r.start_date, r.open_time, r.start_time) == \
        ("2026-07-10", "17:30", "18:30")
    assert r.lineup == ["Reol"]
    # 石崎ひゅーい LIVE 2026, 7/25, 16:30 / 17:30
    h = ev["17273"]
    assert h.title_ja == "石崎ひゅーい LIVE 2026 -City Lights-"
    assert (h.start_date, h.open_time, h.start_time) == \
        ("2026-07-25", "16:30", "17:30")
    # August: 田村ゆかり LOVE♡LIVE, 8/6, 17:00 / 18:00
    y = _august()["17465"]
    assert y.title_ja.startswith("田村ゆかり")
    assert (y.start_date, y.open_time, y.start_time) == \
        ("2026-08-06", "17:00", "18:00")


def test_multishow_day_keeps_earliest_performance():
    # 7/1 フルタの方程式 has two <p class="date"> shows (12:30/13:30 and
    # 17:30/18:30); the event records the earliest performance's times.
    f = _july()["17379"]
    assert f.start_date == "2026-07-01"
    assert (f.open_time, f.start_time) == ("12:30", "13:30")
    # DISH// (ASCII "//" in the act name) must NOT be shredded by lineup split.
    assert _july()["17289"].lineup == ["DISH//"]


def test_year_is_injected_from_page_month_not_hardcoded():
    # Same fixture, a different pinned page year -> the year follows the page.
    evs = LineCubeShibuyaScraper().parse(_load("line_cube_shibuya_live.html"),
                                         month=dt.date(2027, 7, 1))
    by = {e.source_url.rsplit("/", 1)[-1]: e for e in evs}
    assert by["17379"].start_date == "2027-07-01"


def test_absolute_detail_urls_and_venue_meta():
    ev = _july()
    assert ev["17256"].source_url == "https://linecubeshibuya.com/event/17256"
    for e in ev.values():
        assert e.source_url.startswith("https://linecubeshibuya.com/event/")
        assert e.venue_name == "LINE CUBE SHIBUYA（渋谷公会堂）"
        assert e.venue_area == "Shibuya"
        assert e.genres == []


def test_category_policy_mixed_hall():
    # Every booking in this two-month window is a concert/live; the venue
    # publishes no sports/ceremony rows here, so all resolve to MUSIC.
    for e in list(_july().values()) + list(_august().values()):
        assert e.category == Category.MUSIC
    # A synthetic non-concert card (sumo) must be tagged OTHER via is_nonmusic.
    synth = (
        '<article><a href="https://linecubeshibuya.com/event/99999" '
        'class="innArticle">'
        '<li class="month">7</li><li class="day">6</li>'
        '<h3 class="iventTtl">大相撲 夏巡業 渋谷場所</h3>'
        '<p class="subTtl"><span>日本相撲協会</span></p>'
        '<div class="wrapperDate"><p class="date">'
        '<span>開場時間 10:00</span><span>開演時間 11:00</span></p></div>'
        '</a></article>')
    out = LineCubeShibuyaScraper().parse(synth, month=JULY)
    assert len(out) == 1
    assert out[0].category == Category.OTHER


def test_empty_html_returns_nothing_loudly():
    assert LineCubeShibuyaScraper().parse("<html></html>", month=JULY) == []
    assert LineCubeShibuyaScraper().parse("", month=JULY) == []


# ------------------------------------------------------------------ detail
def test_parse_detail_backslash_yen_and_times():
    # Detail prices use the backslash-yen glyph ("\\7,500" == ¥7,500) and
    # 開場/開演 times, neither of which the generic ¥/OPEN parser reads.
    ev = Event(source="line_cube_shibuya",
               source_url="https://linecubeshibuya.com/event/17273",
               title_ja="石崎ひゅーい", category=Category.MUSIC,
               start_date="2026-07-25")
    LineCubeShibuyaScraper().parse_detail(
        _load("line_cube_shibuya_detail.html"), ev)
    assert (ev.open_time, ev.start_time) == ("16:30", "17:30")
    # 全席指定(一般) 7,500 / (学生) 5,500 -> min 5,500, backslash normalised to ¥
    assert ev.price_min == 5500
    assert ev.price_text and "¥7,500" in ev.price_text and "¥5,500" in ev.price_text
    assert ev.is_free is False
    # This event links only to the organiser (not a playguide) -> no ticket links.
    assert ev.ticket_links == []
