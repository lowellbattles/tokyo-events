import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.sogo_tokyo import SogoTokyoScraper

FIX = Path(__file__).parent / "fixtures"
# The day cells carry no month/year, so pin the page month exactly as
# scrape() would (this fixture is the July 2026 calendar page).
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return SogoTokyoScraper().parse(_load("sogo_tokyo_live.html"), month=JULY)


def _by_id(evs):
    # Key by (numeric detail id, date): multi-day runs share one detail id
    # across several dates, each getting its own fragment-disambiguated Event.
    out = {}
    for e in evs:
        did = e.source_url.split("/")[-1].split("#")[0]
        out[(did, e.start_date)] = e
    return out


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    # July 2026 page: 52 distinct promoter bookings, 15 of them multi-day
    # runs (30 date-occurrences) + 37 single-day ones = 67 raw listing rows,
    # minus 5 bookings at venues venues.resolve_venue() can't place (out of
    # our Tokyo/Kanagawa/Chiba/Saitama scope) = 62. (Was 61 at build time;
    # curating the I'M A SHOW curly-apostrophe alias brought one back.)
    assert len(_july()) == 62


def test_field_spotchecks_budokan():
    # 山本彩 LIVE at 武道館, 日本武道館, 2026-07-14, on sale (not sold out).
    ev = _by_id(_july())[("2577", "2026-07-14")]
    assert ev.title_ja == "山本彩 LIVE at 武道館"
    assert ev.venue_name == "日本武道館"
    assert ev.start_date == "2026-07-14"
    assert ev.is_sold_out is False
    assert ev.category == Category.MUSIC
    assert ev.source == "sogo_tokyo"
    assert ev.source_url == \
        "https://sogotokyo.com/live_information/detail/2577"
    assert ev.lineup == ["山本彩"]     # artist differs from title -> kept


def test_sold_out_listing_row_sets_flag():
    # UVERworld THE LIVE 2026, Zepp Haneda(TOKYO), 07/06 -> class="sales soldout"
    ev = _by_id(_july())[("2893", "2026-07-06")]
    assert ev.title_ja == "UVERworld THE LIVE 2026"
    assert ev.is_sold_out is True


def test_out_of_scope_venue_is_skipped():
    # 栃木県総合文化センター (Tochigi) is outside our Tokyo/Kanagawa/Chiba/
    # Saitama scope and unresolved by venues.resolve_venue() -> dropped, not
    # silently kept with invented geography.
    scraper = SogoTokyoScraper()
    evs = scraper.parse(_load("sogo_tokyo_live.html"), month=JULY)
    assert all("栃木県総合文化センター" not in (e.venue_name or "") for e in evs)
    assert any("栃木県総合文化センター" in v for v in scraper.skipped_venues)


def test_multiday_run_gets_distinct_dated_events():
    # KIM JUNSU's XIA tour stop (detail/2892) plays 東京国際フォーラム
    # ホールA on both 07/03 and 07/04 -> two Events, URL fragment-disambiguated
    # (tachikawa_stage_garden / yokohama_arena precedent).
    by = _by_id(_july())
    fri, sat = by[("2892", "2026-07-03")], by[("2892", "2026-07-04")]
    assert fri.source_url == \
        "https://sogotokyo.com/live_information/detail/2892#2026-07-03"
    assert sat.source_url == \
        "https://sogotokyo.com/live_information/detail/2892#2026-07-04"
    assert fri.venue_name == sat.venue_name == "東京国際フォーラム ホールA"


def test_single_day_show_keeps_bare_detail_url():
    ev = _by_id(_july())[("2577", "2026-07-14")]
    assert "#" not in ev.source_url


def test_lineup_only_when_artist_differs_from_title():
    # BEAT AX -SUMMER EDITION 2026-: artist text == title text -> no lineup.
    ev = _by_id(_july())[("2966", "2026-07-10")]
    assert ev.title_ja == "BEAT AX -SUMMER EDITION 2026-"
    assert ev.lineup == []


def test_empty_html_returns_nothing_loudly():
    assert SogoTokyoScraper().parse("<html></html>", month=JULY) == []
    assert SogoTokyoScraper().parse("", month=JULY) == []


def test_no_month_context_returns_nothing():
    # Without the pinned page month, a bare day-of-month cell can't be
    # resolved to a calendar date -> rows are dropped, not guessed at.
    assert SogoTokyoScraper().parse(_load("sogo_tokyo_live.html")) == []


def test_geography_fields_left_for_export_time_resolution():
    ev = _by_id(_july())[("2577", "2026-07-14")]
    assert ev.venue_area is None
    assert ev.address is None
    assert ev.lat is None
    assert ev.lng is None
    assert ev.genres == []


# ------------------------------------------------------------------ detail
def test_parse_detail_yen_suffix_price_times_and_ticket_links():
    # Detail prices are written "8,800円" (円 suffix, no ¥) which the
    # generic ¥-keyed parser misses; times sit under an "OPEN / START" label
    # with no OPEN/START words next to the times themselves.
    ev = Event(source="sogo_tokyo",
               source_url="https://sogotokyo.com/live_information/detail/2577",
               title_ja="山本彩 LIVE at 武道館",
               category=Category.MUSIC, start_date="2026-07-14")
    SogoTokyoScraper().parse_detail(_load("sogo_tokyo_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("17:30", "18:30")
    assert ev.price_min == 8800
    assert ev.is_free is False
    assert ev.price_text == "指定席8,800円（税込）"
    providers = {link["provider"] for link in ev.ticket_links}
    assert providers == {"pia", "lawson", "eplus"}
    assert not ev.is_sold_out   # the TICKET row's SOLD-OUT template comment
                                # must not be mistaken for real page text
