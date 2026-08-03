"""Core test gaps closed (roadmap R16 / TST-1 in docs/architecture-and-
roadmap.md): four previously-untested critical behaviors --
(1) pipeline found=0 loudness (the primary structural canary) and its
    allow_empty escape hatch,
(2) DETAIL_CAP shared-budget arithmetic (pipeline.py ~229-244) when the
    primary listing pass alone already exceeds the cap,
(3) db.upsert's AUTO-default changed-content branch (db.py ~163-168):
    AUTO keeps a human's current status through content churn; PENDING
    (or any non-AUTO default) re-stages it,
(4) EventStore.list_events' four filter kwargs (status/category/
    date_from/date_to), each proven to select independently of the
    others -- the whole CLI `list` surface.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events import pipeline
from tokyo_events.db import EventStore
from tokyo_events.models import Category, Event, ReviewStatus
from tokyo_events.scrapers.base import BaseScraper


# --------------------------------------------------------- (1) loud-zero --
class EmptyScraper(BaseScraper):
    """Yields nothing -- the 'site structure may have changed' canary."""
    source_id = "dummy"
    supports_detail = False

    def scrape(self):
        return []

    def parse(self, html, **context):
        return []


class EmptyAllowedScraper(EmptyScraper):
    """Same empty scrape(), but opts out like festivals/matsuri/museums
    do off-season -- see scrapers/festivals.py, matsuri.py, museums.py."""
    allow_empty = True


def test_found_zero_is_loud_for_a_normal_source(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "loud.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (EmptyScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["found"] == 0
    assert report["error"] is not None
    assert "0 events parsed" in report["error"]


def test_found_zero_is_silent_when_allow_empty(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "quiet.db")
    monkeypatch.setattr(
        pipeline, "SCRAPERS",
        {"dummy": (EmptyAllowedScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["found"] == 0
    assert report["error"] is None


# ---------------------------------------------------- (2) DETAIL_CAP -----
class DetailCapScraper(BaseScraper):
    """45 brand-new, detail-less events -- 5 more than DETAIL_CAP. The
    primary listing pass alone fills to_enrich past the cap, so the
    backlog-drain and sold-out-sweep top-ups (DETAIL_CAP - len(to_enrich))
    must both no-op on a negative/zero limit rather than double-count."""
    source_id = "dummy"
    supports_detail = True
    fetch_calls = 0                  # class-level: pipeline owns the instance

    def scrape(self):
        for i in range(45):
            yield Event(source="dummy", source_url=f"https://d/{i}",
                       title_ja=f"E{i}", category=Category.MUSIC,
                       start_date="2099-01-01")

    def parse(self, html, **context):
        return []

    def fetch(self, url, retries=2):
        DetailCapScraper.fetch_calls += 1
        return "<html>ok</html>"

    def parse_detail(self, html, ev):
        return ev             # unchanged is fine -- only the count matters


def test_detail_cap_boundary_45_candidates_exactly_40_fetches(
        tmp_path, monkeypatch):
    DetailCapScraper.fetch_calls = 0
    store = EventStore(tmp_path / "cap.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (DetailCapScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"])[0]

    assert report["error"] is None
    assert report["found"] == 45
    assert report["new"] == 45
    assert DetailCapScraper.fetch_calls == pipeline.DETAIL_CAP == 40
    assert report["details"] == 40


# --------------------------------------------- (3) upsert AUTO-default ---
def _stored_status(store):
    return store.conn.execute("SELECT status FROM events").fetchone()["status"]


def test_upsert_auto_default_keeps_human_status_through_a_change(tmp_path):
    store = EventStore(tmp_path / "auto.db")
    ev = Event(source="x", source_url="https://x/1", title_ja="A",
              category=Category.MUSIC, start_date="2099-01-01")
    assert store.upsert(ev, ReviewStatus.AUTO) == "new"
    assert _stored_status(store) == "auto"

    store.set_status(ev.dedupe_key(), ReviewStatus.APPROVED)
    assert _stored_status(store) == "approved"

    changed = Event(source="x", source_url="https://x/1", title_ja="A!",
                    category=Category.MUSIC, start_date="2099-01-01")
    assert store.upsert(changed, ReviewStatus.AUTO) == "changed"
    # AUTO-sourced churn must not undo a human's approval
    assert _stored_status(store) == "approved"


def test_upsert_pending_default_restages_the_same_change(tmp_path):
    store = EventStore(tmp_path / "pending.db")
    ev = Event(source="x", source_url="https://x/1", title_ja="A",
              category=Category.MUSIC, start_date="2099-01-01")
    assert store.upsert(ev, ReviewStatus.AUTO) == "new"
    store.set_status(ev.dedupe_key(), ReviewStatus.APPROVED)
    assert _stored_status(store) == "approved"

    changed = Event(source="x", source_url="https://x/1", title_ja="A!",
                    category=Category.MUSIC, start_date="2099-01-01")
    # identical content change, only the default_status differs from above
    assert store.upsert(changed, ReviewStatus.PENDING) == "changed"
    assert _stored_status(store) == "pending"


# ----------------------------------------------------- (4) list_events ---
def _seed_filter_matrix(store):
    """2 statuses x 2 categories x 3 distinct dates (one date shared)."""
    events = {
        "e1": Event(source="x", source_url="https://x/1", title_ja="E1",
                   category=Category.MUSIC, start_date="2099-01-10"),
        "e2": Event(source="x", source_url="https://x/2", title_ja="E2",
                   category=Category.MUSIC, start_date="2099-02-10"),
        "e3": Event(source="x", source_url="https://x/3", title_ja="E3",
                   category=Category.ART, start_date="2099-03-10"),
        "e4": Event(source="x", source_url="https://x/4", title_ja="E4",
                   category=Category.ART, start_date="2099-02-10"),
    }
    for ev in events.values():
        store.upsert(ev, ReviewStatus.PENDING)
    store.set_status(events["e1"].dedupe_key(), ReviewStatus.APPROVED)
    store.set_status(events["e3"].dedupe_key(), ReviewStatus.APPROVED)
    # e2, e4 stay 'pending' (the upsert default above)
    return {k: ev.dedupe_key() for k, ev in events.items()}


def test_list_events_filters_each_select_independently(tmp_path):
    store = EventStore(tmp_path / "filters.db")
    ids = _seed_filter_matrix(store)

    approved = {d["id"] for d in store.list_events(status="approved")}
    assert approved == {ids["e1"], ids["e3"]}

    pending = {d["id"] for d in store.list_events(status="pending")}
    assert pending == {ids["e2"], ids["e4"]}

    music = {d["id"] for d in store.list_events(category="music")}
    assert music == {ids["e1"], ids["e2"]}

    art = {d["id"] for d in store.list_events(category="art")}
    assert art == {ids["e3"], ids["e4"]}

    from_feb10 = {d["id"] for d in store.list_events(date_from="2099-02-10")}
    assert from_feb10 == {ids["e2"], ids["e3"], ids["e4"]}   # excludes e1 (jan)

    to_feb10 = {d["id"] for d in store.list_events(date_to="2099-02-10")}
    assert to_feb10 == {ids["e1"], ids["e2"], ids["e4"]}     # excludes e3 (mar)
