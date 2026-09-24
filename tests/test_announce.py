"""'Just announced' export (announce.py): our first sighting is the
announcement date, except onboarding runs and recorded coverage changes
(backfill), and a promoter fold keeps the EARLIEST sighting. Timestamps
are injected via SQL, so nothing here depends on the wall clock."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokyo_events import announce                              # noqa: E402
from tokyo_events.announce import UNKNOWN, jst_date, sighting_date  # noqa: E402
from tokyo_events.db import EventStore                         # noqa: E402
from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402
from tokyo_events.promoters import _merge                      # noqa: E402


def test_first_seen_is_read_as_a_jst_day():
    # the 07:00 JST cron stamps late-evening UTC of the previous day
    assert jst_date("2026-09-24T22:10:05+00:00") == "2026-09-25"
    assert jst_date("2026-09-24T10:00:00+00:00") == "2026-09-24"
    assert jst_date(None) is None and jst_date("garbage") is None


def test_onboarding_day_and_backfill_are_unknown():
    bf = {"creativeman": "2026-09-25"}
    ts = lambda d: f"{d}T01:00:00+00:00"          # 10:00 JST, same day
    # a source's first-ever run: everything is new TO US, not announced
    assert sighting_date("zepp", ts("2026-07-13"), "2026-07-13", bf) == UNKNOWN
    assert sighting_date("zepp", ts("2026-07-14"), "2026-07-13", bf) \
        == "2026-07-14"
    # backfill cutoff is inclusive; the day after is a real announcement
    assert sighting_date("creativeman", ts("2026-09-25"), "2026-07-14",
                         bf) == UNKNOWN
    assert sighting_date("creativeman", ts("2026-09-26"), "2026-07-14",
                         bf) == "2026-09-26"
    assert sighting_date("creativeman", None, "2026-07-14", bf) == UNKNOWN


def test_promoter_fold_keeps_the_earliest_sighting():
    venue = {"first_seen": "2026-08-01"}
    _merge(venue, {"first_seen": "2026-09-30"})
    assert venue["first_seen"] == "2026-08-01"      # venue knew it first
    venue = {"first_seen": "2026-09-30"}
    _merge(venue, {"first_seen": UNKNOWN})
    assert venue["first_seen"] == UNKNOWN           # unknown beats a date
    plain = {}
    _merge(plain, {"first_seen": "2026-09-30"})     # callers without dates
    assert "first_seen" not in plain


def _ev(url, **kw):
    return Event(source=kw.pop("source", "liquidroom"), source_url=url,
                 title_ja=kw.pop("title_ja", "テスト公演"),
                 venue_name=kw.pop("venue_name", "LIQUIDROOM"),
                 category=Category.MUSIC, start_date="2099-01-10", **kw)


def test_export_sets_announced_only_for_real_announcements(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(announce, "BACKFILL", {})
    store = EventStore(tmp_path / "a.db")
    rows = {"https://x/onboard": "2026-07-13T01:00:00+00:00",
            "https://x/new": "2026-09-20T01:00:00+00:00"}
    for url in rows:
        store.upsert(_ev(url), ReviewStatus.AUTO)
    for url, ts in rows.items():
        store.conn.execute("UPDATE events SET first_seen=? WHERE source_url=?",
                           (ts, url))
    store.conn.commit()
    out = tmp_path / "public.json"
    store.export_public_json(out)
    evs = {e["source_url"]: e for e in
           json.loads(out.read_text(encoding="utf-8"))["events"]}
    assert "announced" not in evs["https://x/onboard"]   # first-run day
    assert evs["https://x/new"]["announced"] == "2026-09-20"
    assert not any("first_seen" in e for e in evs.values())
