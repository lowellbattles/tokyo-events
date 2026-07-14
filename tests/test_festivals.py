"""Tests for the FESTIVALS curated source class (scrapers/festivals.py).

Fixtures (captured 2026-07-14, UTF-8, scrubbed for secrets):
- festival_fuji_rock_live.html            : /artist/index lineup grid (day cols)
- festival_summer_sonic_tokyo_day{1,2,3}_live.html : /en/lineup/tokyo-dayN/
- festival_rock_in_japan_live.json        : /2026/api/get/artist/ JSON feed
- festival_sweet_love_shower_live.html     : /contents/artist/lineup roster
- festival_ultra_japan_live.html           : /lineup (poster image only)

Curated facts (dates/venue/ticket URL) live in the module's ACTIVE_EDITIONS,
not in the fixtures; only lineups are scraped. COUNTDOWN JAPAN has no lineup
fixture on purpose — its 26/27 lineup is unannounced and the live site still
serves the finished 25/26 roster, so it is configured skeleton-only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.venues import resolve_venue, vclass_of
from tokyo_events.scrapers import festivals as F
from tokyo_events.scrapers.festivals import (
    FestivalsScraper, ACTIVE_EDITIONS, DORMANT_EDITIONS,
)

FIX = Path(__file__).parent / "fixtures"
# All six 2026 editions are still upcoming/ongoing relative to this pinned date.
TODAY = "2026-07-14"

EDITIONS = {e.key: e for e in ACTIVE_EDITIONS}

#: fetch URL -> fixture file (mirrors each edition's lineup_targets)
FIXTURE_FOR = {
    "https://www.fujirockfestival.com/artist/index": "festival_fuji_rock_live.html",
    "https://www.summersonic.com/en/lineup/tokyo-day1/":
        "festival_summer_sonic_tokyo_day1_live.html",
    "https://www.summersonic.com/en/lineup/tokyo-day2/":
        "festival_summer_sonic_tokyo_day2_live.html",
    "https://www.summersonic.com/en/lineup/tokyo-day3/":
        "festival_summer_sonic_tokyo_day3_live.html",
    "https://rijfes.jp/2026/api/get/artist/": "festival_rock_in_japan_live.json",
    "https://2026.sweetloveshower.com/contents/artist/lineup":
        "festival_sweet_love_shower_live.html",
    "https://ultrajapan.com/lineup": "festival_ultra_japan_live.html",
}


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _payloads(ed):
    """Pre-fetched {url: text} for one edition, straight from fixtures."""
    return {url: _load(FIXTURE_FOR[url])
            for _day, url in ed.lineup_targets if url in FIXTURE_FOR}


def _events(key):
    ed = EDITIONS[key]
    return FestivalsScraper().build_edition(ed, _payloads(ed), today=TODAY)


class _StubScraper(FestivalsScraper):
    """scrape() without the network — fetch() reads from fixtures."""
    def fetch(self, url, retries=2):
        return _load(FIXTURE_FOR[url])


class _GarbageScraper(FestivalsScraper):
    """Every lineup fetch returns junk — skeletons must still ship."""
    def fetch(self, url, retries=2):
        return "<html></html>"


class _NoFetchScraper(FestivalsScraper):
    """fetch() must never be called (used for the off-season sunset test)."""
    def fetch(self, url, retries=2):
        raise AssertionError(f"fetch() should not be called: {url}")


# ------------------------------------------------------- per-extractor counts
def test_fuji_rock_extractor_counts_and_spotchecks():
    daymap = F.extract_fuji_rock(_load("festival_fuji_rock_live.html"),
                                 EDITIONS["fuji_rock"])
    # column position -> day (NOT the stale 25/26/27 header alt text)
    assert {k: len(v) for k, v in daymap.items()} == {
        "2026-07-24": 80, "2026-07-25": 85, "2026-07-26": 81}
    assert "The xx" in daymap["2026-07-24"]
    assert "MASSIVE ATTACK" in daymap["2026-07-26"]     # <br>-joined name
    # HTML5 entity (&Amacr;) must be decoded, never stored raw.
    assert "TĀL FRY" in daymap["2026-07-26"]
    import re
    assert not any(re.search(r"&[A-Za-z]+;|&#\d+;", n)
                   for v in daymap.values() for n in v)


def test_summer_sonic_extractor_counts_and_spotchecks():
    d1 = F.extract_summer_sonic(
        _load("festival_summer_sonic_tokyo_day1_live.html"),
        EDITIONS["summer_sonic_tokyo"], "2026-08-14")
    d3 = F.extract_summer_sonic(
        _load("festival_summer_sonic_tokyo_day3_live.html"),
        EDITIONS["summer_sonic_tokyo"], "2026-08-16")
    assert len(d1) == 46 and len(d3) == 46
    assert "THE STROKES" in d1 and "BUMP OF CHICKEN" in d1
    assert "LE SSERAFIM" in d3 and "YUKI" in d3


def test_rock_in_japan_extractor_counts_and_spotchecks():
    daymap = F.extract_rock_in_japan(_load("festival_rock_in_japan_live.json"),
                                     EDITIONS["rock_in_japan"])
    assert {k: len(v) for k, v in daymap.items()} == {
        "2026-09-12": 24, "2026-09-13": 24,
        "2026-09-19": 24, "2026-09-20": 24, "2026-09-21": 24}
    assert "IMP." in daymap["2026-09-13"]
    assert "INI" in daymap["2026-09-21"] and "Ado" in daymap["2026-09-21"]


def test_sweet_love_shower_extractor_counts_and_spotchecks():
    names = F.extract_sweet_love_shower(
        _load("festival_sweet_love_shower_live.html"),
        EDITIONS["sweet_love_shower"])
    assert len(names) == 96
    assert "あいみょん" in names and "KANA-BOON" in names


def test_ultra_japan_lineup_is_poster_only():
    # Lineup is a poster image; extractor must yield NO names (never OCR/garbage).
    assert F.extract_ultra_japan(_load("festival_ultra_japan_live.html"),
                                 EDITIONS["ultra_japan"]) == []


# ------------------------------------------------------- day-split event shape
def test_fuji_rock_three_day_events():
    evs = _events("fuji_rock")
    assert len(evs) == 3
    by_date = {e.start_date: e for e in evs}
    assert set(by_date) == {"2026-07-24", "2026-07-25", "2026-07-26"}
    assert [len(by_date[d].lineup) for d in
            ("2026-07-24", "2026-07-25", "2026-07-26")] == [80, 85, 81]
    e0 = by_date["2026-07-24"]
    assert e0.end_date is None                    # per-day event, not a range
    assert e0.title_ja == "FUJI ROCK FESTIVAL '26"
    assert e0.source_url == (
        "https://www.fujirockfestival.com/artist/index#2026-07-24")


def test_rock_in_japan_five_day_events_noncontiguous():
    evs = _events("rock_in_japan")
    assert len(evs) == 5
    assert sorted(e.start_date for e in evs) == [
        "2026-09-12", "2026-09-13", "2026-09-19", "2026-09-20", "2026-09-21"]
    assert all(len(e.lineup) == 24 for e in evs)


def test_summer_sonic_three_day_events_with_per_day_urls():
    evs = _events("summer_sonic_tokyo")
    assert len(evs) == 3
    by_date = {e.start_date: e for e in evs}
    assert [len(by_date[d].lineup) for d in
            ("2026-08-14", "2026-08-15", "2026-08-16")] == [46, 51, 46]
    # distinct per-day pages are used as source_url (not edition_url + #iso)
    assert by_date["2026-08-15"].source_url == (
        "https://www.summersonic.com/en/lineup/tokyo-day2/")


def test_sweet_love_shower_single_multiday_event():
    evs = _events("sweet_love_shower")
    assert len(evs) == 1                          # NO day split -> one Event
    e = evs[0]
    assert (e.start_date, e.end_date) == ("2026-08-28", "2026-08-30")
    assert len(e.lineup) == 96
    assert e.source_url == (
        "https://2026.sweetloveshower.com/contents/artist/lineup")  # no anchor


def test_ultra_japan_single_event_skeleton_lineup():
    evs = _events("ultra_japan")
    assert len(evs) == 1
    e = evs[0]
    assert (e.start_date, e.end_date) == ("2026-09-19", "2026-09-20")
    assert e.lineup == []                         # poster-only -> empty lineup
    assert e.genres == ["electronic"]             # fixed prior


def test_countdown_japan_skeletons_with_dark_day():
    # 26/27 lineup unannounced -> pure skeleton events; NEVER the stale 25/26.
    ed = EDITIONS["countdown_japan"]
    evs = FestivalsScraper().build_edition(ed, {}, today=TODAY)
    starts = sorted(e.start_date for e in evs)
    assert starts == ["2026-12-26", "2026-12-27",
                      "2026-12-29", "2026-12-30", "2026-12-31"]
    assert "2026-12-28" not in starts             # 12/28 is a dark day
    assert all(e.lineup == [] for e in evs)
    assert ed.lineup_targets == () and ed.extractor is None


# ------------------------------------------------------- venue resolution
def test_every_edition_venue_name_resolves_to_its_key():
    for ed in ACTIVE_EDITIONS:
        assert resolve_venue(ed.venue_name) == ed.key, ed.key
        assert vclass_of(ed.key) == "festival"


# ------------------------------------------------------- category invariant
def test_every_event_is_music_festival_from_festivals_source():
    evs = list(_StubScraper().scrape(today=TODAY))
    assert evs
    for e in evs:
        assert e.category is Category.MUSIC_FESTIVAL
        assert e.source == "festivals"


# ------------------------------------------------------- full-run counts
def test_scrape_yields_all_active_editions():
    evs = list(_StubScraper().scrape(today=TODAY))
    # fuji 3 + summer 3 + rij 5 + sls 1 + ultra 1 + countdown 5
    assert len(evs) == 18
    from collections import Counter
    per_title = Counter(e.title_ja for e in evs)
    assert per_title["FUJI ROCK FESTIVAL '26"] == 3
    assert per_title["COUNTDOWN JAPAN 26/27"] == 5


# ------------------------------------------------------- resilience
def test_broken_lineup_fetch_still_yields_curated_skeletons():
    # Every extractor is fed "<html></html>" (via scrape's fetch): curated
    # facts (dates/title/venue) survive, lineups fall back to empty.
    evs = list(_GarbageScraper().scrape(today=TODAY))
    assert len(evs) == 18                          # same skeleton count
    assert all(e.lineup == [] for e in evs)        # no garbage names
    assert all(e.title_ja and e.venue_name and e.start_date for e in evs)


def test_extractors_fail_toward_empty_not_garbage():
    junk = "<html></html>"
    assert F.extract_fuji_rock(junk, EDITIONS["fuji_rock"]) == {
        "2026-07-24": [], "2026-07-25": [], "2026-07-26": []}
    assert F.extract_summer_sonic(junk, EDITIONS["summer_sonic_tokyo"]) == []
    assert F.extract_rock_in_japan("not json", EDITIONS["rock_in_japan"]) == {
        d: [] for d in EDITIONS["rock_in_japan"].dates}
    assert F.extract_sweet_love_shower(junk, EDITIONS["sweet_love_shower"]) == []


def test_finished_editions_are_skipped_offseason():
    # After the last run ends, scrape() yields nothing and never fetches.
    assert list(_NoFetchScraper().scrape(today="2027-06-01")) == []


# ------------------------------------------------------- config guards
def test_scraper_flags_for_pipeline():
    s = FestivalsScraper()
    assert s.source_id == "festivals"
    assert s.allow_empty is True         # seasonal: skips the loud-zero guard
    assert s.supports_detail is False    # no per-event detail pages
    assert s.rate_limit_s >= 2.0         # politeness floor


def test_dormant_editions_documented():
    keys = {d["key"] for d in DORMANT_EDITIONS}
    assert keys == {"japan_jam", "metrock_tokyo", "viva_la_rock",
                    "synchronicity_fes", "greenroom_fes"}
    assert all(d.get("parse_pattern") for d in DORMANT_EDITIONS)
