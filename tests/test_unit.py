"""Tests for the 代官山UNIT (Daikanyama UNIT) scraper.

Fixtures (UTF-8, scrubbed of secrets):
- unit_daikanyama_live.html   : /schedule/ (July 2026) listing, 18 cards
- unit_daikanyama_detail.html : /schedule/14494/ (DOTAMA 4MAN LIVE) detail
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.unit import UnitScraper

FIX = Path(__file__).parent / "fixtures"
# The saved listing is the July 2026 page; pin the page-month anchor so
# year inference is deterministic (matches the /schedule/ served that day).
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.source_url.rstrip("/").split("/")[-1]: e
            for e in UnitScraper().parse(_load("unit_daikanyama_live.html"),
                                         month=JULY)}


# ------------------------------------------------------------------ listing
def test_parses_every_card():
    # 18 event cards in the fixture, all with distinct detail URLs
    evs = list(UnitScraper().parse(_load("unit_daikanyama_live.html"),
                                   month=JULY))
    assert len(evs) == 18
    assert len({e.source_url for e in evs}) == 18


def test_headline_event_fields():
    e = _events()["14494"]
    assert e.title_ja == "DOTAMA 4MAN LIVE 「社交辞令 vol.42」"
    assert e.start_date == "2026-07-04"
    assert e.open_time == "16:15"          # START/price only on the detail page
    assert e.start_time is None
    assert e.price_min is None
    assert e.category is Category.MUSIC
    assert "DOTAMA" in e.lineup and "fishbowl" in e.lineup
    assert e.venue_name == "代官山UNIT" and e.venue_area == "Daikanyama"


def test_detail_url_is_absolute_https():
    e = _events()["14601"]
    assert e.source_url == "https://www.unit-tokyo.com/schedule/14601/"
    assert e.title_ja == "MOS BRASS PIT TOUR"   # no title-top/bottom lines
    for ev in _events().values():
        assert ev.source_url.startswith("https://www.unit-tokyo.com/schedule/")


def test_year_inferred_from_page_month_for_spillover_days():
    # The July grid shows adjacent-month spillover: 06/30 -> June, 08/02 ->
    # August, both of the same (page) year, derived from MM/DD not the URL.
    assert _events()["14534"].start_date == "2026-06-30"   # BOCCHI。, 06/30
    assert _events()["14534"].open_time == "18:00"
    assert _events()["13748"].start_date == "2026-08-02"   # spills into Aug


def test_lineup_splits_on_slashes_only():
    # ｜ pairs a name with its reading and must stay one entry;
    # / and ／ separate co-billed acts.
    assert _events()["14546"].lineup == ["YELLOW 黃宣｜イエロー・ホアンシュエン"]
    assert _events()["14770"].lineup[:3] == ["立花ハジメ", "大野由美子", "小山田圭吾"]


def test_category_policy_all_music():
    # UNIT is a single-purpose live house: the non-music guard trips on none
    # of the fixture's cards (incl. an idol "Fan Meeting", kept as MUSIC).
    assert all(e.category is Category.MUSIC for e in _events().values())


def test_empty_html_returns_no_events():
    # Structural failure must be loud (0 events), never silent garbage.
    assert UnitScraper().parse("<html></html>", month=JULY) == []


# ------------------------------------------------------------------- detail
def test_parse_detail_fills_start_price_and_ticket():
    ev = _events()["14494"]
    UnitScraper().parse_detail(_load("unit_daikanyama_detail.html"), ev)
    assert ev.open_time == "16:15"
    assert ev.start_time == "17:00"               # from the open-door dl
    assert ev.price_min == 3900                    # min of ¥4,900/4,400/3,900
    assert ev.price_text and "¥" in ev.price_text
    providers = {t["provider"]: t for t in ev.ticket_links}
    assert "eplus" in providers
    assert "eplus.jp" in providers["eplus"]["url"]
