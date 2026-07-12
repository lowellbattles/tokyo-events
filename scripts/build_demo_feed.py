#!/usr/bin/env python3
"""Build site/public.json from test fixtures — a working placeholder feed
so the site renders before the first live scrape, and an end-to-end check
that parser output -> store -> export -> frontend contract all line up.

Run: python scripts/build_demo_feed.py
"""

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tokyo_events.db import EventStore                      # noqa: E402
from tokyo_events.models import ReviewStatus                # noqa: E402
from tokyo_events.scrapers.liquidroom import LiquidroomScraper  # noqa: E402
from tokyo_events.scrapers.zepp import ZeppScraper          # noqa: E402
from tokyo_events.scrapers.ogroup import OGroupScraper      # noqa: E402
from tokyo_events.scrapers.billboard import BillboardScraper    # noqa: E402
from tokyo_events.scrapers.pia import (                     # noqa: E402
    ToyosuPitScraper, PiaArenaMMScraper)

FIX = ROOT / "tests" / "fixtures"
TODAY = dt.date(2026, 7, 2)

JOBS = [
    (LiquidroomScraper(), "liquidroom_schedule.html", {}),
    (ZeppScraper("zepp_divercity"), "zepp_schedule.html", {"today": TODAY}),
    (OGroupScraper("oeast"), "ogroup_schedule.html", {"today": TODAY}),
    (BillboardScraper("billboard_tokyo"), "billboard_schedule.html", {}),
    (ToyosuPitScraper(), "toyosu_schedule.html", {"today": TODAY}),
    (PiaArenaMMScraper(), "pia_arena_schedule.html",
     {"month": dt.date(2026, 7, 1)}),
]


def main():
    db_path = Path("/tmp/demo_feed.db")
    db_path.unlink(missing_ok=True)
    store = EventStore(db_path)
    now = dt.datetime.now().isoformat(timespec="seconds")

    for scraper, fixture, ctx in JOBS:
        events = scraper.parse((FIX / fixture).read_text(), **ctx)
        # Enrich the Liquidroom androp-style detail fixture where applicable
        for ev in events:
            store.upsert(ev, ReviewStatus.AUTO)
        store.conn.execute(
            "INSERT INTO scrape_runs (source, started_at, finished_at, "
            "found, new, error) VALUES (?,?,?,?,?,?)",
            (scraper.source_id, now, now, len(events), len(events),
             None if events else "0 events parsed"))
    store.conn.commit()

    out = ROOT / "site" / "public.json"
    n = store.export_public_json(out)
    print(f"demo feed: {n} events -> {out}")
    for s in store.source_health():
        print(f"  {s['source']:>18}: found={s['found']}")


if __name__ == "__main__":
    main()
