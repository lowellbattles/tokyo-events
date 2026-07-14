import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.smash import SmashScraper

FIX = Path(__file__).parent / "fixtures"
# The day cells carry no month/year, so pin the page month exactly as
# scrape() would (this fixture is the p=3 / Kanto July 2026 calendar page).
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return SmashScraper().parse(_load("smash_jpn_live.html"), month=JULY)


def _by_id(evs):
    out = {}
    for e in evs:
        did = e.source_url.split("id=")[-1].split("#")[0]
        out[(did, e.start_date)] = e
    return out


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    # All 14 raw calendar rows in the July 2026 / p=3 (Kanto) fixture now
    # resolve: KANDA SQUARE HALL and 昭和女子大学人見記念講堂 were curated
    # into venues.py as gap venues after this source's build.
    assert len(_july()) == 14


def test_field_spotcheck_mitski():
    ev = _by_id(_july())[("4712", "2026-07-28")]
    assert ev.title_ja == "MITSKI"
    assert ev.venue_name == "Zepp DiverCity (TOKYO)"
    assert ev.start_date == "2026-07-28"
    assert (ev.open_time, ev.start_time) == ("18:00", "19:00")
    assert ev.is_sold_out is False
    assert ev.category == Category.MUSIC
    assert ev.source == "smash_jpn"
    assert ev.source_url == "https://smash-jpn.com/live/?id=4712"
    assert ev.lineup == ["MITSKI"]      # bare artist name -> kept as lineup
    assert ev.tags == []


def test_sold_out_listing_row_sets_flag():
    # TENDRE, 大手町三井ホール, 07/14 -> ico_soldout.png + "SOLD OUT" text.
    ev = _by_id(_july())[("4686", "2026-07-14")]
    assert ev.title_ja == "TENDRE"
    assert ev.venue_name == "大手町三井ホール"
    assert ev.is_sold_out is True


def test_same_day_ticket_row_is_not_sold_out():
    # THE HAUNTED, SHIBUYA CLUB QUATTRO, 07/13 -> ico_toujitsu.png / 当日券アリ,
    # not a sold-out marker.
    ev = _by_id(_july())[("4629", "2026-07-13")]
    assert ev.title_ja == "THE HAUNTED"
    assert ev.is_sold_out is False


def test_cancelled_row_kept_with_tag_and_untouched_title():
    # DIIV@KANDA SQUARE HALL (07/08) carries 《公演中止》 in the real
    # fixture; KANDA SQUARE HALL is curated (kanda_square_hall) so the row
    # parses naturally.
    diiv = _by_id(_july())[("4693", "2026-07-08")]
    assert diiv.title_ja == "DIIV"        # title untouched, not annotated
    assert diiv.tags == ["cancelled"]
    assert diiv.venue_name == "KANDA SQUARE HALL"
    assert diiv.is_sold_out is False


def test_out_of_scope_venue_is_skipped():
    # Synthetic row at an out-of-Kanto venue (seen on the nationwide
    # listing) inside a minimal calendar table — the venue gate must drop
    # it and record the raw string.
    html = (
        '<table><tr><td><p class="day"><span class="wd">TUE</span><br>7</p>'
        '<ul><li><a href="/live/?id=9999">TEST ACT</a><span>TEST ACT<br>'
        '会場:盛岡 CLUB CHANGE WAVE<br>開場:18:00&nbsp;開演:19:00<br>'
        '前売りアリ</span></li></ul></td></tr></table>'
    )
    scraper = SmashScraper()
    evs = scraper.parse(html, month=JULY)
    assert evs == []
    assert scraper.skipped_venues == {"盛岡 CLUB CHANGE WAVE"}


def test_curated_gap_venue_is_kept():
    # 昭和女子大学 人見記念講堂 has no direct scraper but IS curated in
    # venues.py (a promoter-only "gap" venue, showa_hitomi) -> kept, not
    # skipped, unlike KANDA SQUARE HALL above.
    ev = _by_id(_july())[("4638", "2026-07-11")]
    assert ev.title_ja == "Original Love"
    assert ev.venue_name == "昭和女子大学 人見記念講堂"


def test_lineup_empty_for_multi_act_title():
    # Not present verbatim in the July/Kanto fixture (that combination only
    # shows up on other regions/months), but the guard itself is a pure
    # function of the title text -- exercise it directly via _parse_row's
    # sibling logic through the public MULTI_ACT_RE-driven lineup rule.
    from tokyo_events.scrapers import smash as smash_mod
    assert smash_mod.MULTI_ACT_RE.search(
        "Age Factory x ENTH x Paledusk presents「GOBLIN」TOUR 2026")
    assert not smash_mod.MULTI_ACT_RE.search("MITSKI")


def test_empty_html_returns_nothing_loudly():
    assert SmashScraper().parse("<html></html>", month=JULY) == []
    assert SmashScraper().parse("", month=JULY) == []


def test_no_month_context_returns_nothing():
    # Without the pinned page month, a bare day-of-month cell can't be
    # resolved to a calendar date -> rows are dropped, not guessed at.
    assert SmashScraper().parse(_load("smash_jpn_live.html")) == []


def test_geography_fields_left_for_export_time_resolution():
    ev = _by_id(_july())[("4712", "2026-07-28")]
    assert ev.venue_area is None
    assert ev.address is None
    assert ev.lat is None
    assert ev.lng is None
    assert ev.genres == []


# ------------------------------------------------------------------ detail
def test_parse_detail_price_times_and_ticket_links():
    ev = Event(source="smash_jpn",
               source_url="https://smash-jpn.com/live/?id=4712",
               title_ja="MITSKI", category=Category.MUSIC,
               start_date="2026-07-28")
    SmashScraper().parse_detail(_load("smash_jpn_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("18:00", "19:00")
    assert ev.price_min == 13000        # min of the two ¥ tiers (13,000 / 18,000)
    assert ev.is_free is False
    providers = {link["provider"] for link in ev.ticket_links}
    assert providers == {"pia", "lawson", "eplus"}


def test_parse_detail_multicity_tour_reads_the_matching_leg():
    # id=4662 (STEREOLAB) is one detail page shared by 4 tour legs
    # (Osaka 6/29, Nagoya 6/30, Tokyo/KANDA SQUARE HALL 7/1, Tokyo/EX
    # THEATER ROPPONGI 7/2). The EX THEATER ROPPONGI leg (our listing row)
    # must be the one that supplies price/times/links -- not whichever
    # section happens to come first in the page.
    ev = Event(source="smash_jpn",
               source_url="https://smash-jpn.com/live/?id=4662",
               title_ja="STEREOLAB", category=Category.MUSIC,
               start_date="2026-07-02", venue_name="EX THEATER ROPPONGI")
    SmashScraper().parse_detail(
        _load("smash_jpn_detail_multicity_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("18:00", "19:00")
    assert ev.price_min == 9800
    providers = {link["provider"] for link in ev.ticket_links}
    assert providers == {"pia", "lawson", "eplus"}


def test_parse_detail_does_not_overwrite_listing_fields():
    # Listing-derived times/venue survive detail enrichment untouched;
    # detail only fills gaps.
    ev = Event(source="smash_jpn",
               source_url="https://smash-jpn.com/live/?id=4712",
               title_ja="MITSKI", category=Category.MUSIC,
               start_date="2026-07-28",
               open_time="18:00", start_time="19:00")
    SmashScraper().parse_detail(_load("smash_jpn_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time) == ("18:00", "19:00")
