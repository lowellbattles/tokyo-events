"""skipped_venues surfacing: pipeline report -> scrape_runs -> health JSON
-> rolling venue-gap issue body (scripts/report_errors.py)."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tokyo_events import pipeline
from tokyo_events.db import EventStore
from tokyo_events.models import Category, Event, ReviewStatus
from tokyo_events.scrapers.base import BaseScraper

import report_errors


def _ev(url="https://d/1"):
    return Event(source="dummy", source_url=url, title_ja="A",
                 category=Category.MUSIC, start_date="2099-01-01")


class SkippingScraper(BaseScraper):
    source_id = "dummy"
    supports_detail = False

    def __init__(self):
        super().__init__()
        self.skipped_venues = set()

    def scrape(self):
        self.skipped_venues.update({"大阪城ホール", "架空の東京ホール"})
        yield _ev()

    def parse(self, html, **context):
        return [_ev()]


class SkipThenExplodeScraper(SkippingScraper):
    def scrape(self):
        self.skipped_venues.add("盛岡 CLUB CHANGE WAVE")
        yield _ev()
        raise RuntimeError("listing page 2 broke")


# ------------------------------------------------------------ pipeline layer
def test_report_and_scrape_runs_carry_skipped_venues(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (SkippingScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]

    assert report["error"] is None
    assert report["skipped_venues"] == ["大阪城ホール", "架空の東京ホール"]

    row = store.conn.execute(
        "SELECT skipped_venues FROM scrape_runs WHERE source='dummy'"
    ).fetchone()
    assert json.loads(row["skipped_venues"]) == ["大阪城ホール", "架空の東京ホール"]

    health = {h["source"]: h for h in store.source_health()}
    assert health["dummy"]["skipped_venues"] == ["大阪城ホール", "架空の東京ホール"]


def test_skips_survive_a_mid_scrape_crash(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "t.db")
    monkeypatch.setattr(
        pipeline, "SCRAPERS",
        {"dummy": (SkipThenExplodeScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]

    assert report["error"] is not None            # the crash stays loud
    assert report["skipped_venues"] == ["盛岡 CLUB CHANGE WAVE"]


def test_sources_without_the_attr_report_empty(tmp_path, monkeypatch):
    class PlainScraper(BaseScraper):
        source_id = "dummy"
        supports_detail = False

        def scrape(self):
            yield _ev()

        def parse(self, html, **context):
            return [_ev()]

    store = EventStore(tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (PlainScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["skipped_venues"] == []
    row = store.conn.execute(
        "SELECT skipped_venues FROM scrape_runs").fetchone()
    assert row["skipped_venues"] is None
    assert store.source_health()[0]["skipped_venues"] == []


# ------------------------------------------------------------ migration
def test_pre_column_db_is_migrated(tmp_path):
    """A committed events.db predating the column must gain it on open."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE scrape_runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT, found INTEGER, "
        "new INTEGER, changed INTEGER, details_fetched INTEGER, error TEXT)")
    conn.execute(
        "INSERT INTO scrape_runs (source, started_at, found) "
        "VALUES ('old', '2026-01-01T00:00:00', 5)")
    conn.commit()
    conn.close()

    store = EventStore(path)                      # triggers _migrate()
    cols = {r["name"] for r in
            store.conn.execute("PRAGMA table_info(scrape_runs)")}
    assert "skipped_venues" in cols
    assert store.source_health()[0]["skipped_venues"] == []


# ------------------------------------------------------------ issue body
def test_gap_body_lists_gaps_per_source():
    reports = [
        {"source": "creativeman", "skipped_venues": ["架空の東京ホール"]},
        {"source": "smash_jpn", "skipped_venues":
            ["盛岡 CLUB CHANGE WAVE", "盛岡 CLUB CHANGE WAVE"]},
        {"source": "liquidroom"},                 # venue sources lack the key
        {"source": "udo_artists", "skipped_venues": []},
    ]
    body = report_errors.build_gap_body(reports)
    assert "### `creativeman`" in body
    assert "- 架空の東京ホール" in body
    assert body.count("盛岡 CLUB CHANGE WAVE") == 1   # deduped
    assert "udo_artists" not in body              # empty lists omitted
    assert "venues.py" in body                    # points at the fix


def test_gap_body_none_when_clean():
    assert report_errors.build_gap_body(
        [{"source": "a", "skipped_venues": []}, {"source": "b"}]) is None
