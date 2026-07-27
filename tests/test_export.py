"""Export contract tests (roadmap R3 / register EXP-1..5).

The public feed is a forward-looking view, not an archive: past events
(JST), category "other" rows, undated events and internal-only fields
stay in events.db but never reach site/public.json. Event dates far in
the past/future keep these tests clock-independent.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokyo_events.db import EventStore, EXPORT_DROP_FIELDS   # noqa: E402
from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402


def _ev(url, **kw):
    kw.setdefault("title_ja", "テスト公演")
    kw.setdefault("venue_name", "LIQUIDROOM")
    kw.setdefault("category", Category.MUSIC)
    return Event(source=kw.pop("source", "liquidroom"),
                 source_url=url, **kw)


def _store(tmp_path):
    store = EventStore(tmp_path / "x.db")
    for e in (
        _ev("https://x/future", start_date="2099-01-10",
            price_text="前売 ¥5,000", lat=35.0, lng=139.0,
            ticket_url="https://eplus.jp/x", tags=["tag"]),
        _ev("https://x/past", start_date="2020-01-10"),
        # a still-running date RANGE must stay exported via its end_date
        _ev("https://x/range", start_date="2020-05-01",
            end_date="2099-05-01", category=Category.ART),
        _ev("https://x/undated"),
        _ev("https://x/other", start_date="2099-02-02",
            category=Category.OTHER),
    ):
        store.upsert(e, ReviewStatus.AUTO)
    rejected = _ev("https://x/rejected", start_date="2099-03-03")
    store.upsert(rejected, ReviewStatus.AUTO)
    store.set_status(rejected.dedupe_key(), ReviewStatus.REJECTED)
    return store


def _export(tmp_path, store):
    out = tmp_path / "pub.json"
    n = store.export_public_json(out)
    raw = out.read_text(encoding="utf-8")
    return n, raw, json.loads(raw)


def test_export_prunes_past_other_rejected_and_undated(tmp_path):
    n, _raw, data = _export(tmp_path, _store(tmp_path))
    urls = {e["source_url"] for e in data["events"]}
    assert urls == {"https://x/future", "https://x/range"}
    assert n == 2


def test_export_strips_internal_fields_keeps_rendered_ones(tmp_path):
    _n, _raw, data = _export(tmp_path, _store(tmp_path))
    for e in data["events"]:
        for f in EXPORT_DROP_FIELDS:
            assert f not in e, f"{f} leaked into the public feed"
        for f in ("title_ja", "start_date", "end_date", "venue_key",
                  "genres", "artists", "lineup", "ticket_links",
                  "price_min", "is_free", "is_sold_out", "source_url",
                  "venue_name", "category"):
            assert f in e, f"{f} missing from the public feed"


def test_export_sources_slim_generated_at_aware_and_compact(tmp_path):
    store = _store(tmp_path)
    store.conn.execute(
        "INSERT INTO scrape_runs (source, started_at, found, new, changed,"
        " details_fetched, error, skipped_venues) VALUES "
        "('liquidroom', '2026-01-01T00:00:00', 5, 1, 0, 0, NULL,"
        " '[\"unresolved venue\"]')")
    # a RETIRED source's last health row must leave the footer, not show
    # a stale error forever (mot/what_museum, retired 2026-07-27)
    store.conn.execute(
        "INSERT INTO scrape_runs (source, started_at, found, new, changed,"
        " details_fetched, error, skipped_venues) VALUES "
        "('some_retired_source', '2026-01-01T00:00:00', 0, 0, 0, 0,"
        " '403 Forbidden', NULL)")
    store.conn.commit()
    _n, raw, data = _export(tmp_path, store)
    # run internals + raw skipped-venue strings are curation data,
    # not feed data
    assert data["sources"] == [
        {"source": "liquidroom", "found": 5, "error": None}]
    # tz-aware timestamp: the frontend parses it as UTC, renders as JST
    assert data["generated_at"].endswith("+00:00")
    # compact serialization — no pretty-print whitespace
    assert '"events":[' in raw
