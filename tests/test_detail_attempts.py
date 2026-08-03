"""Detail-pass attempt memory (roadmap R17/SCR-3): an event whose detail
page yields nothing parseable stops being refetched daily after two
fruitless attempts — until its content changes, which re-arms it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events import pipeline  # noqa: E402
from tokyo_events.db import EventStore  # noqa: E402
from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402
from tokyo_events.scrapers.base import BaseScraper  # noqa: E402

FETCHES: list[str] = []


def _ev(title="X"):
    return Event(source="dummy", source_url="https://d/1", title_ja=title,
                 category=Category.MUSIC, start_date="2099-01-01")


class BarrenDetailScraper(BaseScraper):
    """Detail pages exist but never contain parseable fields."""
    source_id = "dummy"
    supports_detail = True

    def fetch(self, url, retries=2):
        FETCHES.append(url)
        return "<html>no open/start, no prices, no links</html>"

    def scrape(self):
        yield _ev()

    def parse(self, html, **context):
        return []

    def parse_detail(self, html, ev):
        return ev                       # nothing ever sticks


def _run(store):
    return pipeline.run(store, only=["dummy"])[0]


def test_barren_detail_pages_stop_being_refetched(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (BarrenDetailScraper, ReviewStatus.PENDING)})
    store = EventStore(tmp_path / "d.db")
    FETCHES.clear()

    r1 = _run(store)                    # new event -> attempt 1
    assert r1["details"] == 1
    r2 = _run(store)                    # backlog retry -> attempt 2
    assert r2["details"] == 1
    r3 = _run(store)                    # two strikes: skipped now
    assert r3["details"] == 0
    assert len(FETCHES) == 2

    # content change re-arms the backlog: the page may have grown data
    changed = _ev(title="X — NEW INFO")
    store.upsert(changed, ReviewStatus.PENDING)
    r4 = _run(store)
    assert r4["details"] == 1
    assert len(FETCHES) == 3


def test_prune_drops_rows_for_past_events(tmp_path):
    store = EventStore(tmp_path / "p.db")
    past = Event(source="dummy", source_url="https://d/old", title_ja="old",
                 category=Category.MUSIC, start_date="2020-01-01")
    store.upsert(past, ReviewStatus.AUTO)
    store.note_detail_attempt(past)
    assert store.conn.execute(
        "SELECT COUNT(*) FROM detail_attempts").fetchone()[0] == 1
    store.prune_detail_attempts()
    assert store.conn.execute(
        "SELECT COUNT(*) FROM detail_attempts").fetchone()[0] == 0
