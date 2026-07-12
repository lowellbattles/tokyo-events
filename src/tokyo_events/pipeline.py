"""Pipeline: run registered scrapers -> upsert -> detail-enrich -> report.

Adding a new source = one Scraper subclass (or a new hall id for an
existing family) and one registry line here.

Detail pass: for events that come back NEW or CHANGED from a listing
scrape, fetch the event's own page once to fill missing times/prices and
collect ticket links. Capped per run to stay polite; the backlog drains
across runs because unfetched events stay 'new/changed' only once —
so we also enrich when key fields are missing.
"""

from __future__ import annotations

import datetime as dt
import traceback
from typing import Callable

from .db import EventStore
from .models import Event, ReviewStatus
from .scrapers.base import BaseScraper
from .scrapers.liquidroom import LiquidroomScraper
from .scrapers.zepp import ZeppScraper
from .scrapers.ogroup import OGroupScraper
from .scrapers.billboard import BillboardScraper
from .scrapers.pia import ToyosuPitScraper, PiaArenaMMScraper
from .scrapers.quattro import QuattroScraper
from .scrapers.www import WWWScraper
from .scrapers.duo import DuoScraper
from .scrapers.loft import LoftScraper
from .scrapers.yokohama_arena import YokohamaArenaScraper

# source_id -> (factory, default review status)
# Promote a source to ReviewStatus.AUTO once it has proven reliable.
SCRAPERS: dict[str, tuple[Callable[[], BaseScraper], ReviewStatus]] = {
    "liquidroom":        (LiquidroomScraper,                    ReviewStatus.PENDING),
    "oeast":             (lambda: OGroupScraper("oeast"),       ReviewStatus.PENDING),
    "owest":             (lambda: OGroupScraper("owest"),       ReviewStatus.PENDING),
    "ocrest":            (lambda: OGroupScraper("ocrest"),      ReviewStatus.PENDING),
    "onest":             (lambda: OGroupScraper("onest"),       ReviewStatus.PENDING),
    "zepp_divercity":    (lambda: ZeppScraper("zepp_divercity"), ReviewStatus.PENDING),
    "zepp_haneda":       (lambda: ZeppScraper("zepp_haneda"),   ReviewStatus.PENDING),
    "zepp_shinjuku":     (lambda: ZeppScraper("zepp_shinjuku"), ReviewStatus.PENDING),
    "zepp_yokohama":     (lambda: ZeppScraper("zepp_yokohama"), ReviewStatus.PENDING),
    "billboard_tokyo":   (lambda: BillboardScraper("billboard_tokyo"),
                          ReviewStatus.PENDING),
    "billboard_yokohama": (lambda: BillboardScraper("billboard_yokohama"),
                           ReviewStatus.PENDING),
    "toyosu_pit":        (ToyosuPitScraper,                 ReviewStatus.PENDING),
    "pia_arena_mm":      (PiaArenaMMScraper,                ReviewStatus.PENDING),
    "quattro_shibuya":   (lambda: QuattroScraper("quattro_shibuya"),
                          ReviewStatus.PENDING),
    "www":               (lambda: WWWScraper("www"),        ReviewStatus.PENDING),
    "www_x":             (lambda: WWWScraper("www_x"),      ReviewStatus.PENDING),
    "duo":               (DuoScraper,                       ReviewStatus.PENDING),
    "loft_shinjuku":     (lambda: LoftScraper("loft_shinjuku"),
                          ReviewStatus.PENDING),
    "shelter":           (lambda: LoftScraper("shelter"),   ReviewStatus.PENDING),
    "yokohama_arena":    (YokohamaArenaScraper,             ReviewStatus.PENDING),
}

#: max detail-page fetches per source per run (politeness cap; the
#: backlog drains across daily runs — raising this trades run time for
#: faster backfill, ~2.5s per fetch)
DETAIL_CAP = 40


def _needs_detail(ev: Event) -> bool:
    return not ev.ticket_links or ev.start_time is None or ev.price_min is None


def run(store: EventStore, only: list[str] | None = None,
        fetch_details: bool = True,
        force_status: ReviewStatus | None = None) -> list[dict]:
    reports = []
    for source_id, (factory, registry_status) in SCRAPERS.items():
        if only and source_id not in only:
            continue
        default_status = force_status or registry_status
        report = {"source": source_id, "found": 0, "new": 0, "changed": 0,
                  "unchanged": 0, "details": 0, "error": None}
        started = dt.datetime.now().isoformat(timespec="seconds")
        try:
            scraper = factory()
            to_enrich: list[Event] = []
            for ev in scraper.scrape():
                report["found"] += 1
                outcome = store.upsert(ev, default_status)
                report[outcome] += 1
                if (fetch_details and scraper.supports_detail
                        and outcome in ("new", "changed")
                        and _needs_detail(ev)):
                    to_enrich.append(ev)

            for ev in to_enrich[:DETAIL_CAP]:
                try:
                    html = scraper.fetch(ev.source_url)
                    enriched = scraper.parse_detail(html, ev)
                    store.upsert(enriched, default_status)
                    report["details"] += 1
                except Exception:      # one bad detail page never kills a run
                    continue

            if report["found"] == 0:
                report["error"] = ("0 events parsed — site structure may "
                                   "have changed")
        except Exception:
            report["error"] = traceback.format_exc(limit=3)

        store.conn.execute(
            "INSERT INTO scrape_runs (source, started_at, finished_at, found, "
            "new, changed, details_fetched, error) VALUES (?,?,?,?,?,?,?,?)",
            (source_id, started, dt.datetime.now().isoformat(timespec="seconds"),
             report["found"], report["new"], report["changed"],
             report["details"], report["error"]))
        store.conn.commit()
        reports.append(report)
    return reports
