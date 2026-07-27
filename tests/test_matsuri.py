"""Curated seasonal sources (matsuri + hanabi, scrapers/matsuri.py).
No fixtures: the scraper fetches nothing — dates are curated facts — so
the tests pin config sanity (every entry resolves, parses, carries a
valid category) and the edition->event mechanics."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, SEASONAL_GENRES
from tokyo_events.scrapers.matsuri import (CuratedSeasonalScraper,
                                           FLOWER_EDITIONS,
                                           HANABI_EDITIONS,
                                           MATSURI_EDITIONS,
                                           SeasonalEdition, _events_for)
from tokyo_events.venues import resolve_venue, display_of, vclass_of

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------ config sanity
def test_every_edition_is_well_formed():
    for ed in MATSURI_EDITIONS + HANABI_EDITIONS + FLOWER_EDITIONS:
        assert ed.dates and all(_ISO.match(d) for d in ed.dates), ed.key
        assert list(ed.dates) == sorted(ed.dates), ed.key
        assert ed.url.startswith("http"), ed.key
        # the edition IS a curated venue identity
        assert display_of(ed.key), ed.key
        assert vclass_of(ed.key) == "matsuri", ed.key
        assert resolve_venue(display_of(ed.key)) == ed.key, ed.key
    for ed in MATSURI_EDITIONS:
        assert ed.category is Category.FESTIVAL, ed.key
    for ed in HANABI_EDITIONS:
        assert ed.category is Category.FIREWORKS, ed.key
        # one evening — or an explicit non-contiguous series of evenings
        # (Yokohama Night Flowers short-burst series)
        assert len(ed.dates) == 1 or not ed.contiguous, ed.key
    for ed in FLOWER_EDITIONS:
        assert ed.category is Category.FLOWERS, ed.key


def test_keys_unique_across_all_configs():
    keys = [e.key for e in
            MATSURI_EDITIONS + HANABI_EDITIONS + FLOWER_EDITIONS]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------- mechanics
def _ed(**kw):
    base = dict(key="x", title_ja="T", category=Category.FESTIVAL,
                dates=("2026-08-01",), venue_area="A",
                url="https://example.org/matsuri")
    base.update(kw)
    return SeasonalEdition(**base)


def test_contiguous_run_is_one_range_event():
    evs = list(_events_for(
        _ed(dates=("2026-08-22", "2026-08-23")), "matsuri", "V",
        today="2026-07-27"))
    assert len(evs) == 1
    assert (evs[0].start_date, evs[0].end_date) == \
        ("2026-08-22", "2026-08-23")
    assert evs[0].genres == ["matsuri"]
    assert evs[0].source_url == "https://example.org/matsuri"


def test_noncontiguous_dates_are_separate_events_with_anchors():
    # 酉の市 shape: zodiac days weeks apart -> one event per day
    evs = list(_events_for(
        _ed(dates=("2026-11-05", "2026-11-17", "2026-11-29"),
            contiguous=False), "matsuri", "V", today="2026-07-27"))
    assert [e.start_date for e in evs] == \
        ["2026-11-05", "2026-11-17", "2026-11-29"]
    assert all(e.end_date is None for e in evs)
    assert evs[0].source_url.endswith("#2026-11-05")


def test_finished_editions_sunset():
    evs = list(_events_for(_ed(dates=("2026-07-25",)), "matsuri", "V",
                           today="2026-07-27"))
    assert evs == []
    # partial: only the future zodiac day survives
    evs = list(_events_for(
        _ed(dates=("2026-07-01", "2026-12-01"), contiguous=False),
        "matsuri", "V", today="2026-07-27"))
    assert [e.start_date for e in evs] == ["2026-12-01"]


def test_fireworks_carry_launch_time_and_genre():
    evs = list(_events_for(
        _ed(category=Category.FIREWORKS, dates=("2026-08-15",),
            start_time="19:15"), "hanabi", "V", today="2026-07-27"))
    assert evs[0].start_time == "19:15"
    assert evs[0].genres == ["hanabi"]
    assert evs[0].genres[0] in SEASONAL_GENRES


def test_flower_events_carry_flowers_genre():
    evs = list(_events_for(
        _ed(category=Category.FLOWERS, dates=("2026-11-01", "2026-11-15")),
        "flowers", "V", today="2026-07-27"))
    assert evs[0].genres == ["flowers"]
    assert evs[0].genres[0] in SEASONAL_GENRES


def test_scraper_flags():
    for sid in ("matsuri", "hanabi", "flowers"):
        s = CuratedSeasonalScraper(sid)
        assert s.source_id == sid
        assert s.allow_empty is True      # off-season quiet is legitimate
        assert s.supports_detail is False # nothing to fetch, ever
