"""Hand-entered concerts (scrapers/curated.py): config is the fact base,
nothing is fetched, finished nights sunset, venues resolve at export like
any promoter row. `today` is injected — no wall clock."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokyo_events.models import Category                      # noqa: E402
from tokyo_events.promoters import PROMOTER_SOURCES, apply_promoter_merge  # noqa: E402
from tokyo_events.scrapers.curated import (CURATED_CONCERTS,  # noqa: E402
                                           CuratedConcertsScraper)
from tokyo_events.venues import resolve_venue                  # noqa: E402


def test_yoyo_ma_two_nights_with_verified_facts():
    evs = list(CuratedConcertsScraper().scrape(today="2026-09-24"))
    ma = [e for e in evs if "ヨーヨー・マ" in e.title_ja]
    assert [e.start_date for e in ma] == ["2026-10-25", "2026-10-27"]
    e = ma[0]
    assert (e.open_time, e.start_time) == ("18:20", "19:00")
    assert e.source_url.endswith("/detail/20261025_M_3.html")
    assert ma[1].source_url.endswith("/detail/20261027_M_3.html")
    assert e.category is Category.MUSIC and e.genres == ["classical"]
    assert e.price_min == 12000          # U25 tier excluded from price_min
    assert all(x.is_sold_out for x in ma)
    assert e.lineup[0] == "ヨーヨー・マ"
    assert [s["kind"] for s in e.sales] == ["presale", "general"]
    assert e.sales[1]["opens"] == "2026-07-25T10:00"


def test_finished_nights_sunset_and_nothing_is_fetched():
    s = CuratedConcertsScraper()
    s.fetch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetch"))
    days = [e.start_date for e in s.scrape(today="2026-10-26")
            if "ヨーヨー・マ" in e.title_ja]
    assert days == ["2026-10-27"]
    assert list(s.scrape(today="2099-01-01")) == []
    assert s.allow_empty is True


def test_entries_resolve_and_merge_like_promoter_rows():
    assert "curated_concerts" in PROMOTER_SOURCES
    for c in CURATED_CONCERTS:
        assert resolve_venue(c.venue), c.venue      # curated venue exists
        assert c.verified and all(u.startswith("https://") for _, u in c.dates)
    ev = next(iter(CuratedConcertsScraper().scrape(today="2026-09-24")))
    [d] = apply_promoter_merge([dict(ev.to_json(), id="x", status="auto")])
    assert d["venue_key"] == "suntory_hall"
