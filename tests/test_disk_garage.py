import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.disk_garage import DiskGarageScraper

FIX = Path(__file__).parent / "fixtures"
# Day cards carry only the bare day-of-month in their own `id` -- pin the
# page month exactly as scrape() would (this fixture is the July 2026
# /artist/date/2026-07 calendar page).
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return DiskGarageScraper().parse(_load("disk_garage_live.html"), month=JULY)


def _by_id(evs):
    out = {}
    for e in evs:
        did = e.source_url.rsplit("/", 1)[-1].split("#")[0]
        out[(did, e.start_date)] = e
    return out


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    # 123 raw calendar cards in the July 2026 fixture; 108 resolve through
    # venues.resolve_venue after the 2026-07-15 curation waves (DISK GARAGE
    # alias spellings, Suntory Hall, Pacifico via promoters, etc.); the
    # remaining 15 are genuinely out of Kanto scope or still uncurated.
    assert len(_july()) == 108


def test_field_spotcheck_nightmare():
    ev = _by_id(_july())[("101239", "2026-07-14")]
    assert ev.title_ja == "NIGHTMARE"
    assert ev.venue_name == "Spotify O-EAST"
    assert ev.start_date == "2026-07-14"
    assert ev.start_time == "18:30"
    assert ev.open_time is None          # listing has no OPEN, only START
    assert ev.category == Category.MUSIC
    assert ev.source == "disk_garage"
    assert ev.source_url == "https://diskgarage.com/ticket/detail/101239"
    assert ev.lineup == ["NIGHTMARE"]    # bare artist title -> kept as lineup
    assert ev.is_sold_out is False


def test_field_spotcheck_nakamori_akina():
    ev = _by_id(_july())[("101061", "2026-07-14")]
    assert ev.title_ja == "中森明菜"
    assert ev.venue_name == "東京国際フォーラム ホールA"
    assert ev.start_date == "2026-07-14"
    assert ev.start_time == "19:00"
    assert ev.lineup == ["中森明菜"]


def test_multi_act_title_gets_no_lineup_guess():
    # "ライブナタリー “group_inou × ピーナッツくん × PAS TASTA”" (KT Zepp
    # Yokohama, 07/14) carries the × multi-act marker -> title kept bare,
    # no (wrong) single-artist lineup guess.
    ev = _by_id(_july())[("101678", "2026-07-14")]
    assert ev.title_ja == "ライブナタリー “group_inou × ピーナッツくん × PAS TASTA”"
    assert ev.lineup == []


def test_out_of_scope_venue_is_skipped():
    # Synthetic card at a venue outside our curated registry, in a minimal
    # container matching the real template (date card -> id gives the
    # day-of-month; sibling event card carries title/venue/time in its
    # three "-right-inner" spans) -- the venue gate must drop it and record
    # the raw string.
    html = (
        '<div class="l-second-contents-artist">'
        '<div id="7" class="l-second-contents-artist-date" data-color="">'
        '<span class="ts-h1 eng t-bld">7</span><span class="ts-15">(火)</span>'
        '</div>'
        '<div class="l-second-contents-artist-btn wide flex anim">'
        '<div class="l-second-contents-information-slim-inner flex a-c j-c">'
        '<a href="/ticket/detail/9999" class="mod-bg-link"></a>'
        '<div class="l-second-contents-information-slim-inner-right flex">'
        '<div class="l-second-contents-information-slim-inner-right-inner flex a-c">'
        '<span class="ts-h8 t-bld">TEST ACT</span></div>'
        '<div class="l-second-contents-information-slim-inner-right-inner flex a-c">'
        '<span class="ts-h8">大阪城ホール</span></div>'
        '<div class="l-second-contents-information-slim-inner-right-inner flex a-c">'
        '<span class="ts-h8">18:00 開演</span></div>'
        '</div></div></div></div>'
    )
    scraper = DiskGarageScraper()
    evs = scraper.parse(html, month=JULY)
    assert evs == []
    assert scraper.skipped_venues == {"大阪城ホール"}


def test_curated_venue_is_kept():
    # 東京国際フォーラム ホールA IS curated (tokyo_intl_forum) -> kept, not
    # skipped, unlike the synthetic out-of-scope case above.
    ev = _by_id(_july())[("101061", "2026-07-14")]
    assert ev.venue_name == "東京国際フォーラム ホールA"


def test_empty_html_returns_nothing_loudly():
    assert DiskGarageScraper().parse("<html></html>", month=JULY) == []
    assert DiskGarageScraper().parse("", month=JULY) == []


def test_no_month_context_returns_nothing():
    # Without the pinned page month, a bare day-of-month id can't be
    # resolved to a calendar date -> rows are dropped, not guessed at.
    assert DiskGarageScraper().parse(_load("disk_garage_live.html")) == []


def test_geography_fields_left_for_export_time_resolution():
    ev = _by_id(_july())[("101239", "2026-07-14")]
    assert ev.venue_area is None
    assert ev.address is None
    assert ev.lat is None
    assert ev.lng is None
    assert ev.genres == []


# ------------------------------------------------------------------ detail
def test_parse_detail_price_times_and_ticket_links():
    ev = Event(source="disk_garage",
               source_url="https://diskgarage.com/ticket/detail/101239",
               title_ja="NIGHTMARE", category=Category.MUSIC,
               start_date="2026-07-14")
    DiskGarageScraper().parse_detail(_load("disk_garage_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("17:30", "18:30")
    # Cheapest TAX-INCLUDED tier (standing ¥8,500) -- not the tax-excluded
    # ¥7,728 figure printed right next to it, which would win a blind
    # min() over every ¥ amount in the block. See module docstring.
    assert ev.price_min == 8500
    assert ev.is_free is False
    providers = {link["provider"] for link in ev.ticket_links}
    assert providers == {"eplus"}
    assert ev.is_sold_out is False


def test_parse_detail_does_not_overwrite_listing_fields():
    # Listing-derived times survive detail enrichment untouched; detail
    # only fills gaps.
    ev = Event(source="disk_garage",
               source_url="https://diskgarage.com/ticket/detail/101239",
               title_ja="NIGHTMARE", category=Category.MUSIC,
               start_date="2026-07-14",
               open_time="17:00", start_time="18:00")
    DiskGarageScraper().parse_detail(_load("disk_garage_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("17:00", "18:00")


def test_parse_detail_fills_open_time_without_clobbering_listing_start():
    # The REALISTIC pipeline case: every listing row already carries
    # start_time (never open_time -- see module docstring), so the detail
    # pass must still backfill open_time in that state, and must leave the
    # listing's own start_time alone rather than overwriting it with
    # whatever the detail page's start figure happens to be.
    ev = Event(source="disk_garage",
               source_url="https://diskgarage.com/ticket/detail/101239",
               title_ja="NIGHTMARE", category=Category.MUSIC,
               start_date="2026-07-14",
               start_time="18:30")     # from listing; open_time still None
    DiskGarageScraper().parse_detail(_load("disk_garage_detail_live.html"), ev)
    assert ev.open_time == "17:30"
    assert ev.start_time == "18:30"
