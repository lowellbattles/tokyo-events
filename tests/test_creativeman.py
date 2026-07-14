"""Tests for the CREATIVEMAN promoter-calendar scraper.

Fixtures are real pages saved from the live site (UTF-8, secrets scrubbed):
  * creativeman_live.html            — the July 2026 month calendar grid.
  * creativeman_tour_live.html       — the Paledusk tour page (one Kanto
    leg, SOLD OUT).
  * creativeman_tour_multi_live.html — the HONNE tour page (two legs: an
    Osaka leg + a Tokyo/Kanto leg, so the Kanto filter is exercised).

CREATIVEMAN is a promoter, so the design deviates from the usual two-stage
pattern: the listing pass emits one minimal Event per Kanto calendar row
(shared tour URL), and scrape() fetches each distinct tour page once to
yield one Event per Kanto leg. These tests drive the PURE pieces (parse,
parse_tour, _process, _legs_to_events, _deferred) with injected context, so
they never touch the network or the wall clock.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.creativeman import (
    CreativemanScraper, parse_tour)

FIX = Path(__file__).parent / "fixtures"
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july_rows():
    return CreativemanScraper().parse(_load("creativeman_live.html"), month=JULY)


# ------------------------------------------------------------------ listing
def test_listing_extracts_only_kanto_rows():
    rows = _july_rows()
    # 18 calendar blocks in July; osaka(2) + aichi(1) are dropped -> 15 Kanto.
    assert len(rows) == 15
    artists = {r.title_ja for r in rows}
    # ANTHEM only plays osaka + aichi in this month -> excluded entirely.
    assert "ANTHEM" not in artists
    # HONNE's July osaka date is dropped, but its Tokyo date is kept.
    assert "HONNE" in artists
    # Every row carries the shared tour URL and no venue yet.
    for r in rows:
        assert r.source == "creativeman"
        assert r.source_url.startswith("https://www.creativeman.co.jp/event/")
        assert r.venue_name is None
        assert isinstance(r.category, Category)


def test_listing_dates_and_soldout_badge():
    rows = {r.title_ja: r for r in _july_rows()}
    # Day number + injected month -> ISO date.
    assert rows["Paledusk"].start_date == "2026-07-15"
    # Listing status badges: SOLD OUT -> sold out; 発売中 / 当日券あり -> not.
    assert rows["Paledusk"].is_sold_out is True          # SOLD OUT
    assert rows["iri"].is_sold_out is True               # SOLD OUT
    assert rows["シユイ"].is_sold_out is False            # 当日券あり
    assert rows["GAEREA"].is_sold_out is False           # 発売中


def test_empty_html_returns_nothing_loudly():
    s = CreativemanScraper()
    assert s.parse("<html></html>") == []
    assert s.parse("") == []
    # Without month context, day-only cells can't be dated -> no rows.
    assert s.parse(_load("creativeman_live.html")) == []


# --------------------------------------------------------------- tour parse
def test_tour_single_leg_fields():
    page = parse_tour(_load("creativeman_tour_live.html"))
    assert page["artist"] == "Paledusk"
    assert page["title"] == "Paledusk Who killed Paledusk?? TOUR"
    assert len(page["legs"]) == 1
    leg = page["legs"][0]
    assert leg["pref"] == "東京"
    assert leg["date"] == "2026-07-15"
    assert leg["venue"] == "Zepp Shinjuku"
    assert (leg["open_time"], leg["start_time"]) == ("18:00", "19:00")
    # ￥5,800-(税込/1Drink別): the drink note must NOT undercut the floor.
    assert leg["price_min"] == 5800
    assert leg["sold_out"] is True
    assert leg["guests"] == ["ano"]
    # プレイガイド cell -> pia + eplus + lawson (the docs.google notice link
    # in 注意事項 is not a playguide, so it is ignored).
    assert [l["provider"] for l in leg["ticket_links"]] == \
        ["pia", "eplus", "lawson"]


def test_tour_single_leg_maps_to_one_event():
    page = parse_tour(_load("creativeman_tour_live.html"))
    s = CreativemanScraper()
    url = "https://www.creativeman.co.jp/event/paledusk_whokilled/"
    evs = list(s._legs_to_events(page, url, {"2026-07-15": True}, "Paledusk"))
    assert len(evs) == 1
    ev = evs[0]
    # Title is the tour headline; the artist rides in the lineup with the guest.
    assert ev.title_ja == "Paledusk Who killed Paledusk?? TOUR"
    assert ev.lineup == ["Paledusk", "ano"]
    assert ev.venue_name == "Zepp Shinjuku"      # RAW string, not canonicalized
    assert ev.category == Category.MUSIC
    assert ev.price_min == 5800
    assert ev.is_sold_out is True
    # source_url = tour URL + "#" + ISO date (single leg -> no venue slug).
    assert ev.source_url == url + "#2026-07-15"
    assert s.skipped_venues == set()


def test_tour_multi_leg_keeps_only_kanto_leg():
    page = parse_tour(_load("creativeman_tour_multi_live.html"))
    # Both legs parse (osaka + tokyo); the filter runs in _legs_to_events.
    prefs = {leg["pref"] for leg in page["legs"]}
    assert prefs == {"大阪", "東京"}
    s = CreativemanScraper()
    url = "https://www.creativeman.co.jp/event/honne_2026/"
    evs = list(s._legs_to_events(page, url, {}, "HONNE"))
    # NHK大阪ホール does not resolve -> dropped; SGC HALL 有明 -> kept.
    assert len(evs) == 1
    assert evs[0].venue_name == "SGC HALL 有明"
    assert evs[0].start_date == "2026-07-22"
    assert evs[0].source_url == url + "#2026-07-22"
    # The osaka leg is out of Kanto scope, so it is NOT flagged for curation.
    assert s.skipped_venues == set()


def test_uncurated_kanto_hall_is_skipped_and_reported():
    # Rewrite the Tokyo leg's venue to a made-up hall that resolve_venue does
    # not know: the leg is dropped, and its raw string is collected for the
    # integrator (a Kanto miss, unlike the osaka leg which is silently out).
    html = _load("creativeman_tour_multi_live.html").replace(
        "SGC HALL 有明", "架空の東京ホール")
    page = parse_tour(html)
    s = CreativemanScraper()
    evs = list(s._legs_to_events(page, "https://x/", {}, "HONNE"))
    assert evs == []
    assert s.skipped_venues == {"架空の東京ホール"}


def test_floor_date_drops_past_month_legs():
    # A tour page lists every leg, including ones in an already-passed month
    # that the forward month-walk never showed. floor_date drops them so the
    # DB isn't seeded with stale past events.
    page = parse_tour(_load("creativeman_tour_multi_live.html"))   # legs in July
    s = CreativemanScraper()
    # Floor past both July legs -> nothing kept.
    assert list(s._legs_to_events(page, "https://x/", {}, "HONNE",
                                  floor_date="2026-08-01")) == []
    # Floor at the July window start -> the Kanto leg survives.
    kept = list(s._legs_to_events(page, "https://x/", {}, "HONNE",
                                  floor_date="2026-07-01"))
    assert [e.venue_name for e in kept] == ["SGC HALL 有明"]


def test_two_kanto_legs_same_date_get_distinct_urls():
    # Synthesize a collision: two curated Kanto legs on one date must not
    # collapse to the same source_url.
    page = {
        "title": "TWIN NIGHT", "artist": "X",
        "legs": [
            {"pref": "東京", "date": "2026-08-01", "venue": "Zepp Shinjuku",
             "open_time": None, "start_time": None, "price_text": None,
             "price_min": None, "is_free": None, "ticket_links": [],
             "guests": [], "sold_out": False},
            {"pref": "東京", "date": "2026-08-01", "venue": "LIQUIDROOM",
             "open_time": None, "start_time": None, "price_text": None,
             "price_min": None, "is_free": None, "ticket_links": [],
             "guests": [], "sold_out": False},
        ],
    }
    s = CreativemanScraper()
    urls = {e.source_url for e in s._legs_to_events(page, "https://t/", {}, "X")}
    assert urls == {
        "https://t/#2026-08-01-zepp_shinjuku",
        "https://t/#2026-08-01-liquidroom",
    }


# -------------------------------------------------------- cap / deferral
def test_cap_zero_defers_every_row_with_date_fragment():
    rows = _july_rows()
    s = CreativemanScraper(tour_fetch_cap=0)
    evs = list(s._process(rows))          # no tour fetch happens at cap 0
    # Every Kanto calendar row becomes a minimal deferred Event.
    assert len(evs) == len(rows) == 15
    for ev in evs:
        assert "#" in ev.source_url
        assert ev.source_url.endswith("#" + ev.start_date)
        assert ev.venue_name is None      # venue only known from the tour page
    # A deferred row's URL equals what the enriched single leg will produce,
    # so the next run's upsert fills it in instead of duplicating.
    pale = next(e for e in evs if e.title_ja == "Paledusk")
    tour = parse_tour(_load("creativeman_tour_live.html"))
    enriched = list(CreativemanScraper()._legs_to_events(
        tour, "https://www.creativeman.co.jp/event/paledusk_whokilled/",
        {}, "Paledusk"))[0]
    assert pale.source_url == enriched.source_url


class _FakeScraper(CreativemanScraper):
    """Serves fixtures for the two known tours (by slug) and a legless page
    for every other tour, counting how many tour pages it fetched."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tour_fetches = 0

    def fetch(self, url, retries=2):
        if "paledusk" in url:
            self.tour_fetches += 1
            return _load("creativeman_tour_live.html")
        if "honne" in url:
            self.tour_fetches += 1
            return _load("creativeman_tour_multi_live.html")
        self.tour_fetches += 1
        return "<html><body></body></html>"   # unknown tour -> no legs


def test_process_enriches_known_tours_and_honours_cap():
    rows = _july_rows()
    s = _FakeScraper(tour_fetch_cap=25)
    evs = {e.venue_name: e for e in s._process(rows)
           if e.venue_name is not None}
    # Paledusk's Kanto leg is fully enriched from its tour page.
    pale = evs["Zepp Shinjuku"]
    assert pale.price_min == 5800
    assert pale.is_sold_out is True
    assert pale.start_time == "19:00"
    assert pale.source_url.endswith("#2026-07-15")
    # HONNE keeps the Kanto leg only.
    assert "SGC HALL 有明" in evs
    assert not any("大阪" in (v or "") for v in evs)
    # Distinct July tour URLs (13) all fit under the cap of 25.
    assert s.tour_fetches <= 25

    # A tight cap fetches at most `cap` tour pages; the rest defer (no venue).
    s2 = _FakeScraper(tour_fetch_cap=3)
    evs2 = list(s2._process(_july_rows()))
    assert s2.tour_fetches == 3
    assert any(e.venue_name is None for e in evs2)     # deferred rows exist
