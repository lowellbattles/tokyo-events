"""Tests for the 新宿ReNY / RUIDO family scraper.

Fixtures are raw HTML saved from ruido.org (2026-07-13):
  reny_shinjuku_live.html      — July 2026 month-index (flyer grid, 31 links)
  reny_shinjuku_live_aug.html  — August 2026 month-index (aug/24 is a flyer
                                 with NO href -> must be skipped, 10 links)
  reny_shinjuku_detail.html    — the jul/13 detail page (bracket labels)

The month-index carries no text, so the listing only knows each event's
detail URL + date; parse_detail() supplies the real title/lineup/times/price.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.reny import RenyScraper

FIX = Path(__file__).parent / "fixtures"
JUL = dt.date(2026, 7, 1)
AUG = dt.date(2026, 8, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _jul():
    return {e.source_url.split("/")[-1]: e
            for e in RenyScraper("reny_shinjuku").parse(
                _load("reny_shinjuku_live.html"), month=JUL)}


# ------------------------------------------------------------------ listing
def test_july_index_parses_every_flyer_link():
    # 31 distinct <a href="jul/..."> flyer links in the July grid.
    assert len(_jul()) == 31


def test_listing_placeholder_title_and_date_from_href_and_context():
    e = _jul()["13.html"]
    assert e.title_ja == "新宿ReNY 7/13公演"   # pipeline detail pass replaces
    assert e.start_date == "2026-07-13"        # day 13 (href) + July (context)
    assert e.category == Category.MUSIC
    assert e.venue_name == "新宿ReNY"
    assert e.venue_area == "Shinjuku"


def test_detail_urls_are_absolute_https_joined_to_month_dir():
    evs = _jul()
    e = evs["13.html"]
    assert e.source_url == "https://ruido.org/reny/2026/7/jul/13.html"
    assert all(ev.source_url.startswith("https://ruido.org/reny/2026/7/jul/")
               for ev in evs.values())


def test_same_day_suffixed_events_stay_distinct():
    evs = _jul()
    # jul/4-1.html and jul/4-2.html are two different shows on the 4th.
    assert "4-1.html" in evs and "4-2.html" in evs
    assert evs["4-1.html"].source_url != evs["4-2.html"].source_url
    assert evs["4-1.html"].start_date == evs["4-2.html"].start_date == \
        "2026-07-04"
    # jul/12 has three shows (-1/-2/-3), all on the 12th.
    twelfths = [k for k in evs if k.startswith("12-")]
    assert len(twelfths) == 3
    assert all(evs[k].start_date == "2026-07-12" for k in twelfths)


def test_august_index_skips_flyer_without_href():
    # aug/24 is a <div> flyer with no <a href> (hold/TBA) -> not an event.
    evs = {e.source_url.split("/")[-1]: e
           for e in RenyScraper("reny_shinjuku").parse(
               _load("reny_shinjuku_live_aug.html"), month=AUG)}
    assert len(evs) == 10
    assert "24.html" not in evs
    assert evs["1-7.html"].start_date == "2026-08-01"
    assert evs["30.html"].source_url == \
        "https://ruido.org/reny/2026/8/aug/30.html"


def test_empty_html_yields_no_events_loud_failure():
    assert RenyScraper("reny_shinjuku").parse("<html></html>", month=JUL) == []


def test_unknown_hall_rejected():
    import pytest
    with pytest.raises(ValueError):
        RenyScraper("reny_nowhere")


# ------------------------------------------------------------------- detail
def _detail_event():
    ev = Event(source="reny_shinjuku",
               source_url="https://ruido.org/reny/2026/7/jul/13.html",
               title_ja="新宿ReNY 7/13公演", category=Category.MUSIC,
               start_date="2026-07-13", venue_name="新宿ReNY")
    return RenyScraper("reny_shinjuku").parse_detail(
        _load("reny_shinjuku_detail.html"), ev)


def test_detail_overwrites_placeholder_title_and_fills_lineup():
    e = _detail_event()
    assert e.title_ja == "～シンセレ2026～"        # ［TITLE］ replaces placeholder
    assert len(e.lineup) == 13                     # ／-separated ［ACT］
    assert e.lineup[0] == "Answers"
    assert "BLVCKBERRY" in e.lineup
    assert e.lineup[-1] == "20SI."


def test_detail_open_start_and_prices():
    e = _detail_event()
    assert (e.open_time, e.start_time) == ("15:00", "15:30")  # ［OPEN / START］
    assert e.price_min == 4500                                # ［ADV / DOOR］ adv
    assert "4,500" in e.price_text
    # the "(D代別途要)" drink note carries no ¥ -> never becomes a price tier


def test_detail_ticket_link_captured():
    e = _detail_event()
    providers = {t["provider"] for t in e.ticket_links}
    assert "eplus" in providers
    url = next(t["url"] for t in e.ticket_links if t["provider"] == "eplus")
    assert url == "https://eplus.jp/sf/venue/1601720"
    assert e.ticket_url == url
