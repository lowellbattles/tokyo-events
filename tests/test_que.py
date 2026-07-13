"""Tests for the 下北沢CLUB Que scraper (clubque.net).

Fixtures are raw HTML saved live 2026-07-13:
- que_shimokitazawa_live.html         : the 2026/07 monthly schedule listing
- que_shimokitazawa_detail_live.html  : the /schedule/15119/ detail page
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.que import QueScraper

FIX = Path(__file__).parent / "fixtures"
JUL = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.source_url.rstrip("/").split("/")[-1]: e
            for e in QueScraper().parse(_load("que_shimokitazawa_live.html"),
                                        month=JUL)}


# ------------------------------------------------------------- listing parse
def test_parses_exact_event_count():
    # 27 <article id="entry..."> rows on the 2026/07 page.
    assert len(_events()) == 27


def test_title_subtitle_lineup_and_date():
    e = _events()["15119"]
    assert e.title_ja == "Dope Flamingo｜フーテン族｜Johnny Yoshi Hiro"
    assert e.subtitle == "発明の力!"          # quoted series name, quotes stripped
    assert e.start_date == "2026-07-01"       # from the entryYYYYMMDD id
    assert e.lineup == ["Dope Flamingo", "フーテン族", "Johnny Yoshi Hiro"]
    assert e.venue_name == "下北沢CLUB Que"
    assert e.venue_area == "Shimokitazawa"
    assert e.category == Category.MUSIC


def test_detail_urls_are_absolute_https():
    evs = _events()
    assert evs["15119"].source_url == "https://clubque.net/schedule/15119/"
    assert all(e.source_url.startswith("https://clubque.net/schedule/")
               for e in evs.values())


def test_sold_out_marker_detected_and_stripped_from_title():
    # listing heading: "宮本浩次 -oneman-【SOLD OUT】"
    e = _events()["16124"]
    assert e.is_sold_out
    assert e.title_ja == "宮本浩次 -oneman-"     # 【SOLD OUT】 removed
    assert "SOLD OUT" not in e.title_ja


def test_streaming_tag_flagged():
    # 2026/07/17 carries <li class="streaming">配信あり</li>
    assert _events()["15132"].tags == ["streaming"]
    # a plain night has no streaming tag
    assert _events()["15119"].tags == []


def test_vs_billing_not_split_into_lineup():
    # "A vs B" battle bills use "vs", not the ｜ separator -> single title,
    # no false artist split.
    e = _events()["15540"]
    assert e.title_ja == "自爆 vs DOGO"
    assert e.lineup == []


# --------------------------------------------------- category policy (pure LH)
def test_all_listing_rows_are_music():
    # Que is a pure live house (no sports/expos/ceremonies). The defensive
    # is_nonmusic guard must not misclassify any real concert row.
    assert all(e.category == Category.MUSIC for e in _events().values())


# ------------------------------------------------------------ loud failure
def test_empty_html_yields_no_events():
    assert QueScraper().parse("<html></html>") == []
    assert QueScraper().parse("") == []


# ---------------------------------------------------------- detail enrichment
def test_detail_fills_times_price_and_ticket_links():
    ev = Event(source="que_shimokitazawa",
               source_url="https://clubque.net/schedule/15119/",
               title_ja="Dope Flamingo｜フーテン族｜Johnny Yoshi Hiro",
               start_date="2026-07-01")
    QueScraper().parse_detail(_load("que_shimokitazawa_detail_live.html"), ev)
    # OPEN／START "18:30／19:00" — full-width slash parsed as two times
    assert (ev.open_time, ev.start_time) == ("18:30", "19:00")
    assert ev.price_min == 4000                 # ADV.￥4,000 (not ￥4,500 door)
    providers = {t["provider"] for t in ev.ticket_links}
    assert {"eplus", "livepocket"} <= providers
    lp = next(t for t in ev.ticket_links if t["provider"] == "livepocket")
    assert lp["url"] == "https://livepocket.jp/e/que20260701"


def test_detail_ignores_pickup_sidebar_sold_out():
    # The detail page's "PICK UP" sidebar lists OTHER events, one of which is
    # 【SOLD OUT】. That must not mark THIS (available) event sold out.
    ev = Event(source="que_shimokitazawa",
               source_url="https://clubque.net/schedule/15119/",
               title_ja="Dope Flamingo｜フーテン族｜Johnny Yoshi Hiro",
               start_date="2026-07-01")
    QueScraper().parse_detail(_load("que_shimokitazawa_detail_live.html"), ev)
    assert ev.is_sold_out is False


def test_detail_does_not_overwrite_existing_listing_fields():
    ev = Event(source="que_shimokitazawa",
               source_url="https://clubque.net/schedule/15119/",
               title_ja="X", start_date="2026-07-01",
               open_time="12:00", start_time="13:00", price_min=999)
    QueScraper().parse_detail(_load("que_shimokitazawa_detail_live.html"), ev)
    assert (ev.open_time, ev.start_time, ev.price_min) == ("12:00", "13:00", 999)
