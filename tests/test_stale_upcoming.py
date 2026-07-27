"""Stale-upcoming surfacing (roadmap R8): approved events a HEALTHY
source stopped listing are reported — possible cancellations — but
never auto-hidden (month-window scrapers legitimately drop far-future
events).

Flow under test: db.stale_upcoming query -> pipeline attaches rows only
to sources whose run just succeeded -> report_errors renders the
rolling issue body.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tokyo_events import pipeline  # noqa: E402
from tokyo_events.db import EventStore  # noqa: E402
from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402
from tokyo_events.scrapers.base import BaseScraper  # noqa: E402

import report_errors  # noqa: E402

OLD = "2026-07-01T00:00:00"      # long before any plausible cutoff


def _ev(url, **kw):
    kw.setdefault("title_ja", "テスト公演")
    kw.setdefault("category", Category.MUSIC)
    return Event(source=kw.pop("source", "dummy"), source_url=url, **kw)


def _age(store, ev):
    store.conn.execute("UPDATE events SET last_seen=? WHERE id=?",
                       (OLD, ev.dedupe_key()))
    store.conn.commit()


def test_stale_upcoming_query(tmp_path):
    store = EventStore(tmp_path / "s.db")
    stale = _ev("https://x/stale", start_date="2099-01-10")
    fresh = _ev("https://x/fresh", start_date="2099-01-11")
    past = _ev("https://x/past", start_date="2020-01-01")
    onview = _ev("https://x/onview", start_date="2020-05-01",
                 end_date="2099-05-01", category=Category.ART)
    pending = _ev("https://x/pending", start_date="2099-02-01")
    for e in (stale, fresh, past, onview):
        store.upsert(e, ReviewStatus.AUTO)
    store.upsert(pending, ReviewStatus.PENDING)
    for e in (stale, past, onview, pending):
        _age(store, e)

    got = {r["id"]: r for r in store.stale_upcoming(days=3)}
    # flagged: the unseen upcoming event AND the unseen still-running
    # range event; not flagged: fresh, already-past, never-published
    assert set(got) == {stale.dedupe_key(), onview.dedupe_key()}
    row = got[stale.dedupe_key()]
    assert row["source"] == "dummy"
    assert row["start_date"] == "2099-01-10"
    assert row["title"] == "テスト公演"
    assert row["last_seen"] == OLD[:10]


class OneEventScraper(BaseScraper):
    """Yields only event A — anything else stored for this source goes
    unseen this run."""
    source_id = "dummy"
    supports_detail = False

    def scrape(self):
        yield _ev("https://x/a", start_date="2099-03-01")

    def parse(self, html, **context):
        return []


class ExplodingScraper(OneEventScraper):
    def scrape(self):
        yield _ev("https://x/a", start_date="2099-03-01")
        raise RuntimeError("listing broke")


def _seed_stale_b(store):
    b = _ev("https://x/b", start_date="2099-03-02")
    store.upsert(b, ReviewStatus.AUTO)
    _age(store, b)
    return b


def test_pipeline_attaches_stale_rows_on_healthy_runs(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "p.db")
    b = _seed_stale_b(store)
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (OneEventScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["error"] is None
    assert [r["id"] for r in report["stale_upcoming"]] == [b.dedupe_key()]


def test_pipeline_suppresses_stale_rows_when_source_errored(tmp_path,
                                                            monkeypatch):
    store = EventStore(tmp_path / "p.db")
    _seed_stale_b(store)
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (ExplodingScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["error"] is not None
    assert report["stale_upcoming"] == []   # broken source ≠ cancellations


def test_stale_issue_body_lists_rows_with_reject_hint():
    reports = [{"source": "udo_artists", "stale_upcoming": [
        {"id": "abc123", "source": "udo_artists",
         "start_date": "2026-07-31", "last_seen": "2026-07-23",
         "title": "GLAY"}]},
               {"source": "clean", "stale_upcoming": []}]
    body = report_errors.build_stale_body(reports)
    assert "GLAY" in body and "abc123" in body and "2026-07-31" in body
    assert "cli.py reject" in body
    assert "### `udo_artists`" in body
    assert report_errors.build_stale_body(
        [{"source": "clean", "stale_upcoming": []}]) is None
