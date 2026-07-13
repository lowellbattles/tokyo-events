import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.club_citta import ClubCittaScraper

FIX = Path(__file__).parent / "fixtures"
# The listing fixture is the current-month (July 2026) page; pin the month so
# day-of-month cards resolve deterministically.
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = ClubCittaScraper().parse(_load("club_citta_live.html"), month=JULY)
    return {e.source_url.split("/")[-1]: e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_count_and_skips_private():
    # 24 cards in the fixture; the 4 "PRIVATE"/貸し切り rental days are skipped.
    ev = _july()
    assert len(ev) == 20
    # the four private-rental day URLs must not appear
    for pid in ("1720", "1666", "1718", "1719"):
        assert pid not in ev


def test_field_spotchecks_from_fixture():
    ev = _july()
    # betcover!! × Hedigan's, 14 Tue, OPEN 18:00 / START 19:00
    b = ev["1655"]
    assert b.title_ja == 'CLUB CITTA\' Presents "いいこと" betcover!! × Hedigan\'s'
    assert (b.start_date, b.open_time, b.start_time) == \
        ("2026-07-14", "18:00", "19:00")
    assert b.lineup[:2] == ["betcover!!", "Hedigan's"]
    # 浅香唯LIVE2026, 17 Fri, OPEN 18:15 / START 19:00
    a = ev["1642"]
    assert a.title_ja.startswith("浅香唯LIVE2026")
    assert (a.start_date, a.open_time, a.start_time) == \
        ("2026-07-17", "18:15", "19:00")


def test_sold_out_and_start_only_times():
    ev = _july()
    assert ev["1656"].is_sold_out is True          # きんとき「THANK YOU SOLD OUT！」
    assert ev["1739"].is_sold_out is True          # FUJI ROCK SPECIAL / TURNSTILE
    # 「START 18:00」 only (no OPEN listed)
    assert ev["1665"].open_time is None
    assert ev["1665"].start_time == "18:00"


def test_month_context_dates_past_and_future_days():
    ev = _july()
    # "fix" (already-past) card day 1 resolves to July 1, not next month
    assert ev["1587"].start_date == "2026-07-01"
    # upcoming card day 30 resolves within the same pinned month
    assert ev["1648"].start_date == "2026-07-30"


def test_absolute_detail_urls_and_venue_meta():
    ev = _july()
    assert ev["1655"].source_url == "https://clubcitta.co.jp/schedule/1655"
    for e in ev.values():
        assert e.source_url.startswith("https://clubcitta.co.jp/schedule/")
        assert e.category == Category.MUSIC
        assert e.venue_name == "CLUB CITTA'（クラブチッタ）"
        assert e.venue_area == "Kawasaki"


def test_empty_html_returns_nothing_loudly():
    assert ClubCittaScraper().parse("<html></html>", month=JULY) == []
    assert ClubCittaScraper().parse("", month=JULY) == []


# ------------------------------------------------------------------ detail
def test_parse_detail_price_times_tickets():
    # CLUB CITTA' prices are "N,NNN円" (no ¥); the custom parse_detail reads
    # the 座種/料金 row, drops the per-person drink charge, and keeps eplus.
    ev = Event(source="club_citta",
               source_url="https://clubcitta.co.jp/schedule/1655",
               title_ja="betcover", category=Category.MUSIC,
               start_date="2026-07-14")
    ClubCittaScraper().parse_detail(_load("club_citta_detail.html"), ev)
    assert (ev.open_time, ev.start_time) == ("18:00", "19:00")
    # 前売り 5,000 / 【U-20】3,500 -> min 3,500; the 600円 drink charge excluded
    assert ev.price_min == 3500
    assert ev.price_text and "5,000円" in ev.price_text
    assert {"provider": "eplus",
            "url": "https://eplus.jp/iikoto0714/",
            "code": None} in ev.ticket_links
