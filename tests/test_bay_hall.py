import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.bay_hall import BayHallScraper

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 7, 13)   # pin 'today' so any inference is deterministic


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return {e.source_url: e
            for e in BayHallScraper().parse(_load("yokohama_bay_hall_live.html"),
                                            today=TODAY)}


# ------------------------------------------------------------------- listing
def test_parses_all_public_events_and_skips_private():
    evs = _july()
    # 10 articles in the July fixture, 2 of them class="private" (rentals).
    assert len(evs) == 8
    assert not any((e.title_ja or "").upper() == "PRIVATE" for e in evs.values())


def test_listing_fields_and_absolute_detail_urls():
    evs = _july()
    daisy = evs["https://bayhall.jp/schedule/2026/07/9428/"]
    assert daisy.title_ja == "DAISY TOWN"
    assert daisy.start_date == "2026-07-04"          # <h2> July + day 04
    assert daisy.category == Category.MUSIC
    assert daisy.venue_name == "横浜ベイホール"
    assert daisy.venue_area == "Shinyamashita"

    hey = evs["https://bayhall.jp/schedule/2026/07/9041/"]
    assert hey.title_ja == "HEY-SMITH"
    assert hey.start_date == "2026-07-22"

    # every detail URL is absolute https on the venue's own domain
    assert all(u.startswith("https://bayhall.jp/schedule/") for u in evs)


def test_cancelled_show_tagged_and_title_cleaned():
    # <h3>RICKEY TROOPER【公演中止】</h3> -> clean title + cancelled tag,
    # still a MUSIC event (cancellation is a status, not a category).
    ev = _july()["https://bayhall.jp/schedule/2026/07/9312/"]
    assert ev.title_ja == "RICKEY TROOPER"
    assert ev.tags == ["cancelled"]
    assert ev.category == Category.MUSIC
    assert ev.start_date == "2026-07-19"


def test_month_archive_page_parses_from_its_own_heading():
    # /2026/08/ archive page: 7 articles, 1 private -> 6 events, all August,
    # dated off the page's own "08 August 2026" heading (no month kwarg).
    evs = BayHallScraper().parse(_load("yokohama_bay_hall_month_live.html"))
    assert len(evs) == 6
    assert all((e.start_date or "").startswith("2026-08") for e in evs)
    silent = next(e for e in evs if e.title_ja == "SILENT SIREN")
    assert silent.start_date == "2026-08-01"
    assert silent.source_url == "https://bayhall.jp/schedule/2026/08/8989/"


def test_empty_html_returns_no_events_loud_failure():
    assert BayHallScraper().parse("<html></html>") == []


# -------------------------------------------------------------------- detail
def test_parse_detail_times_price_and_ticket_links():
    ev = Event(source="yokohama_bay_hall",
               source_url="https://bayhall.jp/schedule/2026/07/9428/",
               title_ja="DAISY TOWN", start_date="2026-07-04")
    enriched = BayHallScraper().parse_detail(
        _load("yokohama_bay_hall_detail.html"), ev)
    # dd reads "OPEN 15:00 / CLOSE 21:00" -> OPEN captured, no START present
    assert enriched.open_time == "15:00"
    assert enriched.start_time is None
    # CHARGE tiers ¥15,000 / ¥9,000 / ¥5,000 -> floor 5,000; drink ¥ excluded
    assert enriched.price_min == 5000
    # LivePocket (livepocket.jp/e/, not the t.livepocket.jp host textutils knows)
    providers = {t["provider"] for t in enriched.ticket_links}
    assert "livepocket" in providers
    assert all(t["url"].startswith("https://") for t in enriched.ticket_links)
    assert not enriched.is_sold_out


def test_parse_detail_never_overwrites_listing_data():
    ev = Event(source="yokohama_bay_hall", source_url="x",
               title_ja="DAISY TOWN", open_time="17:30", start_time="18:30",
               price_min=6000, price_text="¥6,000")
    enriched = BayHallScraper().parse_detail(
        _load("yokohama_bay_hall_detail.html"), ev)
    assert (enriched.open_time, enriched.start_time) == ("17:30", "18:30")
    assert enriched.price_min == 6000
