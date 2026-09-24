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
    CreativemanScraper, parse_artist_page, parse_tour)

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


def test_two_night_leg_header_venue_cleanup():
    # "2026/12/11(金)・12(日) 東京ドーム" — the second-day remnant must not
    # poison the venue string (it used to drop the whole leg as
    # unresolvable "・12(日) 東京ドーム")
    from tokyo_events.scrapers.creativeman import _EXTRA_DAY_RE
    assert _EXTRA_DAY_RE.sub("", "・12(日) 東京ドーム").strip() == "東京ドーム"
    assert _EXTRA_DAY_RE.sub("", "・12（日）東京ドーム").strip() == "東京ドーム"
    # normal venues pass through
    assert _EXTRA_DAY_RE.sub("", "日本武道館") == "日本武道館"


# -------------------------------------------------- microsites (2026-09-24)
# creativeman_202706_live.html: the June 2027 calendar, whose five
# RADIOHEAD rows link OFF-SITE to radiohead2027.jp
# (creativeman_microsite_radiohead_live.html) — a microsite with no leg
# tables; dates/venue are images, named only in the hero img alt text.
JUNE27 = dt.date(2027, 6, 1)
RH_URL = "https://www.radiohead2027.jp/"


class _MicrositeScraper(CreativemanScraper):
    def __init__(self, page, **kw):
        super().__init__(**kw)
        self.page = page

    def fetch(self, url, retries=2):
        return self.page


def _rh_rows():
    rows = CreativemanScraper().parse(_load("creativeman_202706_live.html"),
                                      month=JUNE27)
    return [r for r in rows if r.title_ja == "RADIOHEAD"]


def test_microsite_rows_take_the_single_named_venue():
    rows = _rh_rows()
    assert [r.start_date for r in rows] == [
        "2027-06-08", "2027-06-09", "2027-06-11", "2027-06-12", "2027-06-13"]
    assert all(r.source_url == RH_URL for r in rows)
    s = _MicrositeScraper(_load("creativeman_microsite_radiohead_live.html"))
    evs = list(s._process(rows))
    assert len(evs) == 5
    assert {e.venue_name for e in evs} == {"GMOアリーナさいたま"}
    assert evs[0].source_url == RH_URL + "#2027-06-08"
    assert evs[0].title_ja == "RADIOHEAD"
    assert evs[0].lineup == ["RADIOHEAD"]
    assert evs[0].price_min is None        # ¥ tiers stay unparsed: honest gap
    assert not s.skipped_venues


def test_microsite_without_a_named_venue_is_reported_not_dropped():
    s = _MicrositeScraper("<html><head><title>X TOUR</title></head>"
                          "<body><img alt='X 2027'></body></html>")
    evs = list(s._process(_rh_rows()))
    assert len(evs) == 5 and all(e.venue_name is None for e in evs)
    [msg] = s.skipped_venues
    assert msg.startswith("[no leg table] RADIOHEAD") and RH_URL in msg


# --------------------------------------- /artist/ headliner pages (2026-09-24)
# Newer headliner-tour template (no leg <table>s -> parse_tour finds zero
# legs -> parse_artist_page is tried before the microsite fallback). Four
# real pages saved as fixtures, each exercising a different quirk:
#   * evanescence — two-night leg via the "/12.2" slash-restated span, a
#     Support Act line, a 一般発売日 WITH a 10:00am time.
#   * weezer      — five single-night legs across cities (Kanto filter),
#     a 一般発売日 with NO time, and (unrendered) leftover VIP-upsell
#     markup copy-pasted from the evanescence template.
#   * ironmaiden  — two-night leg via the bare "25" continuation span (no
#     slash, no month — inherits the first night's month), and a Lコード
#     embedded in the playguide anchor's own text.
#   * a7x         — every price tier marked soldout (whole-leg sold out),
#     and a closed-out presale anchor elsewhere on the page that points at
#     a stale eplus.jp/dreamtheater/ URL from an earlier announcement —
#     ticket_links must not pick that up.
def _artist_page(name):
    return parse_artist_page(_load(name))


def test_artist_page_evanescence_two_night_leg_and_support_act():
    page = _artist_page("creativeman_artist_evanescence_live.html")
    assert page["artist"] == "EVANESCENCE"
    # Tokyo (2 nights) + Osaka = 3 legs; JA-only (section#info-en, right
    # after, is NOT double-counted).
    assert len(page["legs"]) == 3
    tokyo = [l for l in page["legs"] if l["venue"] == "SGC HALL ARIAKE"]
    assert [l["date"] for l in tokyo] == ["2026-12-01", "2026-12-02"]
    leg = tokyo[0]
    assert leg["pref"] == "東京"
    assert (leg["open_time"], leg["start_time"]) == ("18:00", "19:00")
    assert leg["price_min"] == 16500
    assert leg["sold_out"] is False
    assert leg["guests"] == ["Ave Mujica"]
    assert [l["provider"] for l in leg["ticket_links"]] == \
        ["eplus", "pia", "lawson"]
    # Both nights share venue/times/price (same leg, split one-per-night).
    assert tokyo[1]["price_min"] == 16500
    assert (tokyo[1]["open_time"], tokyo[1]["start_time"]) == ("18:00", "19:00")
    # sales: kept for a future feature only, never fed into Event/DB.
    assert leg["sales"]["general_on_sale"] == "07-18 10:00"
    assert {"label", "opens", "closes"} <= leg["sales"]["windows"][0].keys()
    assert any(w["label"] == "オフィシャル先行" for w in leg["sales"]["windows"])


def test_artist_page_weezer_multi_city_kanto_filter_and_no_time_onsale():
    page = _artist_page("creativeman_artist_weezer_live.html")
    assert page["artist"] == "WEEZER"
    assert len(page["legs"]) == 5
    prefs = {l["pref"] for l in page["legs"]}
    assert prefs == {"東京", "京都", "大阪", "愛知"}
    tokyo = [l for l in page["legs"] if l["pref"] == "東京"]
    assert [l["date"] for l in tokyo] == ["2027-02-12", "2027-02-13"]
    assert all(l["venue"] == "東京ガーデンシアター" for l in tokyo)
    # 一般発売日：9/5(土) has no time -> no time in the normalized string.
    assert tokyo[0]["sales"]["general_on_sale"] == "09-05"
    # Leftover evanescence VIP-upsell markup (real eplus.jp/evanescence
    # link, "Tour/Premium Upgrade" price tiers) must not leak in.
    for leg in page["legs"]:
        assert leg["price_min"] in (16000, 18000)
        assert all("eplus.jp/evanescence" not in (l.get("url") or "")
                  for l in leg["ticket_links"])
        assert "Upgrade" not in (leg["price_text"] or "")

    # Kanto filter at _legs_to_events: only the two Tokyo legs survive.
    s = CreativemanScraper()
    evs = list(s._legs_to_events(page, "https://x/weezer/", {}, "WEEZER"))
    assert len(evs) == 2
    assert {e.start_date for e in evs} == {"2027-02-12", "2027-02-13"}
    assert all(e.venue_name == "東京ガーデンシアター" for e in evs)
    assert all(e.price_min == 16000 for e in evs)
    assert s.skipped_venues == set()          # non-Kanto prefs, not reported


def test_artist_page_ironmaiden_bare_day_continuation_and_l_code():
    page = _artist_page("creativeman_artist_ironmaiden_live.html")
    assert page["artist"] == "IRON MAIDEN"
    assert len(page["legs"]) == 2
    assert [l["date"] for l in page["legs"]] == ["2026-11-24", "2026-11-25"]
    assert all(l["venue"] == "Kアリーナ横浜" for l in page["legs"])
    assert all(l["pref"] == "神奈川" for l in page["legs"])
    leg = page["legs"][0]
    assert leg["price_min"] == 15000
    codes = [l["code"] for l in leg["ticket_links"] if l["code"]]
    assert "L75989" in codes


def test_artist_page_a7x_whole_leg_soldout_no_stale_link_leak():
    page = _artist_page("creativeman_artist_a7x_live.html")
    assert page["artist"] == "AVENGED SEVENFOLD"
    assert len(page["legs"]) == 1
    leg = page["legs"][0]
    assert leg["date"] == "2026-09-30"
    assert leg["venue"] == "SGC HALL ARIAKE"
    assert leg["price_min"] == 17500
    # Every price tier carries the soldout class -> the whole leg is out.
    assert leg["sold_out"] is True
    # A closed presale anchor elsewhere on the page points at a stale
    # eplus.jp/dreamtheater/ URL (an earlier co-headline announcement) —
    # ticket_links must only carry THIS leg's three real playguide links.
    assert [l["provider"] for l in leg["ticket_links"]] == \
        ["eplus", "pia", "lawson"]
    assert all("dreamtheader" not in (l.get("url") or "").lower()
              and "dreamtheater" not in (l.get("url") or "").lower()
              for l in leg["ticket_links"])
    assert leg["ticket_links"][0]["url"] == "https://eplus.jp/avengedsevenfold/"


def test_process_falls_back_to_artist_page_when_no_leg_tables():
    # parse_tour finds zero <table> legs on this template; _process must
    # try parse_artist_page before the microsite heuristic.
    class _ArtistPageScraper(CreativemanScraper):
        def fetch(self, url, retries=2):
            return _load("creativeman_artist_evanescence_live.html")

    rows = [Event(source="creativeman",
                  source_url="https://www.creativeman.co.jp/artist/2026/12evanescence/",
                  title_ja="EVANESCENCE", category=Category.MUSIC,
                  start_date="2026-12-01")]
    s = _ArtistPageScraper()
    evs = list(s._process(rows))
    assert len(evs) == 2                      # the two Kanto (Tokyo) legs
    assert all(e.venue_name == "SGC HALL ARIAKE" for e in evs)
    assert {e.start_date for e in evs} == {"2026-12-01", "2026-12-02"}
    ev = next(e for e in evs if e.start_date == "2026-12-01")
    assert ev.price_min == 16500
    assert ev.lineup == ["EVANESCENCE", "Ave Mujica"]
    # source_url identity matches the plain tour-URL + "#date" convention,
    # so a row a previous run deferred (cap or microsite fallback) resolves
    # to the same event instead of duplicating.
    assert ev.source_url == \
        "https://www.creativeman.co.jp/artist/2026/12evanescence/#2026-12-01"
    assert s.skipped_venues == set()
    # "sales" never reaches the Event model (schema changes are a separate
    # owner decision) — Event has no such field to begin with.
    assert not hasattr(ev, "sales")
