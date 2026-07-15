import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.livenation import LiveNationScraper

FIX = Path(__file__).parent / "fixtures"


def _raw():
    return (FIX / "livenation_jp_live.json").read_text(encoding="utf-8")


def _parse():
    """Fresh scraper + its skipped_venues, parsed over the live fixture.
    The fixture is a scrubbed real /api/search/events (CountryIds=110)
    page: 5 resolvable Kanto docs, 2 Kanto-but-uncurated docs (ベルーナ
    ドーム / パシフィコ横浜), 2 non-Kanto JP docs (Kobe / Osaka) and 1 real
    non-JP doc (siteId 38, Berlin) for the exclusion path."""
    sc = LiveNationScraper()
    return sc, sc.parse(_raw())


def _by_id(events):
    out = {}
    for e in events:
        eid = e.source_url.split("/show/")[1].split("/")[0]
        out[eid] = e
    return out


# --------------------------------------------------------------- parse count
def test_parses_expected_event_count():
    # 7 resolvable Kanto documents become events (Belluna Dome and
    # Pacifico Yokohama were curated into venues.py on 2026-07-15); the
    # other 3 (2 non-Kanto JP, 1 non-JP) are filtered out.
    _sc, evs = _parse()
    assert len(evs) == 7


def test_all_events_are_music_category():
    _sc, evs = _parse()
    assert {e.category for e in evs} == {Category.MUSIC}
    assert all(e.source == "livenation_jp" for e in evs)


# ----------------------------------------------------------- JP-only filter
def test_non_jp_document_is_excluded():
    # The Berlin doc (siteId 38, venue.country "Germany") must never
    # surface as an event, nor be logged as a Kanto curation gap.
    sc, evs = _parse()
    assert all(e.venue_name != "Theater am Potsdamer Platz" for e in evs)
    assert not any("Potsdamer" in v for v in sc.skipped_venues)


def test_non_jp_siteid_filtered_in_isolation():
    # A single non-JP document parses to nothing and touches nothing.
    sc = LiveNationScraper()
    doc = {
        "id": "1", "siteId": 4, "eventDate": "2026-09-01T00:00:00Z",
        "venue": {"name": "The O2", "city": "London", "country": "United Kingdom"},
        "lineup": [{"name": "Some Act", "type": "headline", "isPrimary": True}],
        "localizations": [{"cultureName": "ja-JP", "url": "https://x/show/1/a/london/2026-09-01/ja"}],
    }
    assert sc.parse('{"documents": [%s]}' % _json(doc)) == []
    assert sc.skipped_venues == set()


# -------------------------------------------------- Kanto venue gate split
def test_kanto_uncurated_venues_collected_as_gaps():
    # The two Kanto gaps this fixture originally exposed (Belluna Dome,
    # Pacifico Yokohama) were curated into venues.py on 2026-07-15, so
    # they now parse as events and nothing in this fixture is left to
    # report as a curation gap.
    sc, _evs = _parse()
    assert sc.skipped_venues == set()


def test_non_kanto_jp_venues_dropped_silently():
    # Kobe / Osaka docs are Japan but out of the Tokyo/Kanto scope: no
    # event AND, unlike Kanto gaps, NOT recorded in skipped_venues.
    sc, evs = _parse()
    assert all(e.venue_name not in ("GLION ARENA KOBE", "大阪城ホール")
               for e in evs)
    assert "GLION ARENA KOBE" not in sc.skipped_venues
    assert "大阪城ホール" not in sc.skipped_venues


# ------------------------------------------------------------- field checks
def test_field_spotcheck_tokyo_dome():
    ev = _by_id(_parse()[1])["1655882"]
    assert ev.title_ja == "超特急 東京ドーム公演"
    assert ev.venue_name == "東京ドーム"
    assert ev.start_date == "2026-11-25"
    assert ev.end_date is None                      # single-day run
    assert ev.is_sold_out is False                  # allTicketStatus 1
    assert ev.category == Category.MUSIC
    assert ev.source == "livenation_jp"
    assert ev.lineup == ["超特急 東京ドーム公演"]
    # canonical event page on the sibling livenation.co.jp domain, ja locale
    assert ev.source_url == (
        "https://www.livenation.co.jp/show/1655882/超特急/tokyo/2026-11-25/ja")
    assert ev.ticket_url == ev.source_url


def test_budokan_is_surfaced():
    # A real win: 日本武道館 shows, which Budokan's own site doesn't list,
    # come through Live Nation as a normal resolvable Kanto venue.
    ev = _by_id(_parse()[1])["1649622"]
    assert ev.venue_name == "日本武道館"
    assert ev.start_date == "2026-11-24"


def test_sold_out_status_3_sets_flag():
    events = _by_id(_parse()[1])
    assert events["1649641"].is_sold_out is True    # M!LK, 東京ガーデンシアター
    assert events["1649210"].is_sold_out is True    # 超特急, Kアリーナ横浜


def test_on_sale_status_1_not_sold_out():
    ev = _by_id(_parse()[1])["1682571"]             # 粗品, Zepp Shinjuku
    assert ev.is_sold_out is False


def test_multiday_run_sets_end_date():
    ev = _by_id(_parse()[1])["1649210"]             # 超特急, Kアリーナ 8/8-9
    assert ev.start_date == "2026-08-08"
    assert ev.end_date == "2026-08-09"


def test_geography_and_genre_fields_left_for_export():
    ev = _by_id(_parse()[1])["1655882"]
    assert ev.venue_area is None
    assert ev.address is None
    assert ev.lat is None
    assert ev.lng is None
    assert ev.genres == []                          # genres.py tags at export


def test_multi_artist_lineup_shape():
    # The Weeknd doc (headline + support) sits at an uncurated Kanto venue
    # so it yields no event, but the title/lineup extractor is exercised
    # directly on its exact structure.
    sc = LiveNationScraper()
    doc = {
        "lineup": [
            {"name": "The Weeknd", "type": "headline", "isPrimary": True},
            {"name": "CREEPY NUTS", "type": "support", "isPrimary": False},
        ],
    }
    title, lineup = sc._title_and_lineup(doc)
    assert title == "The Weeknd"                    # headline entry
    assert lineup == ["The Weeknd", "CREEPY NUTS"]  # all acts, in order


# -------------------------------------------------------- loud on breakage
def test_malformed_json_raises():
    sc = LiveNationScraper()
    for bad in ("", "not json", "[]", "null", '{"foo": 1}'):
        with pytest.raises(ValueError):
            sc.parse(bad)


def test_empty_documents_list_yields_nothing():
    # A structurally valid but empty page is not an error at parse level
    # (scrape() is the one that treats an empty first page as loud).
    assert LiveNationScraper().parse('{"documents": [], "total": 0}') == []


def _json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
