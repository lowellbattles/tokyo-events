"""Tests for the 東京国際フォーラム (Tokyo International Forum) scraper.

Fixtures are real pages saved from the live site (UTF-8):
  * tokyo_intl_forum_live.html    — the July 2026 whole-complex month page.
  * tokyo_intl_forum_detail.html  — the detail page for the XIA concert
    (id=20260703_0414), which is in ホールA.

The listing is the whole 8-hall complex (title + date + detail id only);
the hall / times / price live only on the detail page. So the listing pass
emits every public event provisionally, and the detail pass makes the
definitive MUSIC-vs-OTHER call by reading the 会場 (hall).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.tif import TokyoIntlForumScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _listing():
    evs = TokyoIntlForumScraper().parse(_load("tokyo_intl_forum_live.html"))
    return {e.source_url.split("id=")[-1]: e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_count_and_skips_industry_only():
    ev = _listing()
    # 54 date-stubs in the fixture collapse to 38 unique events after
    # de-duplicating multi-day runs and dropping the two 関係者-only
    # (industry/staff, not open to the public) conferences.
    assert len(ev) == 38
    # BizReach Conference (7/1-7/2, 関係者 only) and the 横河 user
    # conference (7/23, 関係者 only) must be dropped entirely.
    assert "20260701Fr-H202607047" not in ev   # BizReach
    assert "20260723Fr-H202607103" not in ev   # 横河 ユーザーカンファレンス


def test_field_spotchecks_from_fixture():
    ev = _listing()
    # XIA (id 20260703_0414): multi-day 7/3-7/4, repeated on both dates.
    xia = ev["20260703_0414"]
    assert xia.title_ja == "2026 XIA 6TH ASIA TOUR CONCERT 'GRAVITY' IN TOKYO"
    assert xia.start_date == "2026-07-03"
    assert xia.end_date == "2026-07-04"        # multi-day (repeated 7/3, 7/4)

    # Multi-line listing title (<br>) is joined with a space.
    yumi = ev["20260701_0728"]
    assert yumi.title_ja == (
        "FORUM8 presents YUMI MATSUTOYA THE WORMHOLE TOUR 2025-26")
    assert (yumi.start_date, yumi.end_date) == ("2026-07-01", "2026-07-02")


def test_multiday_span_uses_earliest_and_latest_dates():
    ev = _listing()
    # ピーター・パン runs 7/27-7/31 (stub repeats on all five dates).
    peter = ev["20260727_0202"]
    assert peter.start_date == "2026-07-27"
    assert peter.end_date == "2026-07-31"


def test_absolute_detail_urls_and_complex_meta():
    ev = _listing()
    xia = ev["20260703_0414"]
    assert xia.source_url == (
        "https://www.t-i-forum.co.jp/visitors/event/detail.html?id=20260703_0414")
    for e in ev.values():
        assert e.source_url.startswith(
            "https://www.t-i-forum.co.jp/visitors/event/detail.html?id=")
        assert e.source == "tokyo_intl_forum"
        # Before the detail pass resolves the hall, every event carries the
        # whole-complex venue name and is provisionally MUSIC (the hall gate
        # in parse_detail is what demotes non-Hall-A / non-concert rows).
        assert e.venue_name == "東京国際フォーラム"
        assert e.venue_area == "Yurakucho"
        assert isinstance(e.category, Category)


def test_empty_html_returns_nothing_loudly():
    s = TokyoIntlForumScraper()
    assert s.parse("<html></html>") == []
    assert s.parse("") == []


# ------------------------------------------------------------------ detail
def test_detail_hall_a_concert_stays_music():
    ev = Event(
        source="tokyo_intl_forum",
        source_url=("https://www.t-i-forum.co.jp/visitors/event/"
                    "detail.html?id=20260703_0414"),
        title_ja="XIA", category=Category.MUSIC, start_date="2026-07-03")
    TokyoIntlForumScraper().parse_detail(
        _load("tokyo_intl_forum_detail.html"), ev)

    assert ev.category == Category.MUSIC
    assert ev.venue_name == "東京国際フォーラム ホールA"
    # Authoritative date range from the 開催日時 heading.
    assert (ev.start_date, ev.end_date) == ("2026-07-03", "2026-07-04")
    # 開場/開演 of the first day (full-width colons folded via NFKC).
    assert (ev.open_time, ev.start_time) == ("17:30", "18:30")
    # 料金: "VIP席 25,300円 / 指定席 14,300円" -> min 14,300; the ※-notes and
    # the fan-club URL after them are excluded from the price facts.
    assert ev.price_min == 14300
    assert ev.price_text and "14,300円" in ev.price_text
    assert "オフィシャルHP" not in (ev.price_text or "")
    # The fan-club link is not a playguide, so no ticket links are invented.
    assert ev.ticket_links == []
    # Cleaner title comes from the detail <h1>.
    assert ev.title_ja == "2026 XIA 6TH ASIA TOUR CONCERT 'GRAVITY' IN TOKYO"


def test_detail_non_hall_a_becomes_other_reads_venue_section_not_nav():
    # Rewrite ONLY the 会場-section hall link (unique absolute URL) to Hall C,
    # leaving the header nav's many /facilities/a/ links intact. A correct
    # parse scopes to <main>'s 会場 section, so this must resolve to Hall C
    # and drop the event to OTHER even though the title is a real concert —
    # we surface Hall A only.
    html = _load("tokyo_intl_forum_detail.html").replace(
        '<a href="https://www.t-i-forum.co.jp/visitors/facilities/a/">ホールA</a>',
        '<a href="https://www.t-i-forum.co.jp/visitors/facilities/c/">ホールC</a>')
    ev = Event(
        source="tokyo_intl_forum",
        source_url=("https://www.t-i-forum.co.jp/visitors/event/"
                    "detail.html?id=20260703_0414"),
        title_ja="XIA", category=Category.MUSIC, start_date="2026-07-03")
    TokyoIntlForumScraper().parse_detail(html, ev)

    assert ev.category == Category.OTHER
    assert ev.venue_name == "東京国際フォーラム ホールC"


def test_detail_nonmusic_title_in_hall_a_is_other():
    # An award ceremony (caught by tu.is_nonmusic) booked into Hall A is kept
    # OTHER even though the hall IS A — hall A AND not-nonmusic are both
    # required for MUSIC. (parse_detail overwrites the title from the <h1>,
    # so we rewrite the fixture's title rather than the passed-in one.)
    html = _load("tokyo_intl_forum_detail.html").replace(
        "2026 XIA 6TH ASIA TOUR CONCERT 'GRAVITY' IN TOKYO",
        "日本アカデミー賞 表彰式")
    ev = Event(
        source="tokyo_intl_forum",
        source_url=("https://www.t-i-forum.co.jp/visitors/event/"
                    "detail.html?id=20260703_0414"),
        title_ja="dummy", category=Category.MUSIC, start_date="2026-07-03")
    TokyoIntlForumScraper().parse_detail(html, ev)
    assert ev.title_ja == "日本アカデミー賞 表彰式"
    assert ev.category == Category.OTHER
