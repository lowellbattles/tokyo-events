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
from .scrapers.unit import UnitScraper
from .scrapers.ex_theater import ExTheaterScraper
from .scrapers.bluenote import BlueNoteTokyoScraper, CottonClubScraper
from .scrapers.stellar_ball import StellarBallScraper
from .scrapers.club_citta import ClubCittaScraper
from .scrapers.eggman import EggmanScraper
from .scrapers.shibuya_dive import ShibuyaDiveScraper
from .scrapers.reny import RenyScraper
from .scrapers.que import QueScraper
from .scrapers.bay_hall import BayHallScraper
from .scrapers.fever import FeverScraper
from .scrapers.veats import VeatsScraper
from .scrapers.seata import SeataScraper
from .scrapers.line_cube import LineCubeShibuyaScraper
from .scrapers.hulic_hall import HulicHallScraper
from .scrapers.kanadevia import KanadeviaHallScraper
from .scrapers.sgc_hall import SgcHallScraper
from .scrapers.tif import TokyoIntlForumScraper
from .scrapers.nhk_hall import NHKHallScraper
from .scrapers.opera_city import OperaCityScraper
from .scrapers.tachikawa_sg import TachikawaStageGardenScraper
from .scrapers.orchard_hall import OrchardHallScraper
from .scrapers.tokyo_dome import TokyoDomeScraper
from .scrapers.garden_theater import GardenTheaterScraper
from .scrapers.ariake_arena import AriakeArenaScraper
from .scrapers.toyota_arena import ToyotaArenaScraper
from .scrapers.k_arena import KArenaScraper
from .scrapers.yoyogi import YoyogiScraper
from .scrapers.kokuritsu_stadium import KokuritsuStadiumScraper
from .scrapers.makuhari_messe import MakuhariMesseScraper
from .scrapers.yokohama_buntai import YokohamaBuntaiScraper

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
    "loft_heaven":       (lambda: LoftScraper("loft_heaven"),
                          ReviewStatus.PENDING),
    "yokohama_arena":    (YokohamaArenaScraper,             ReviewStatus.PENDING),
    # --- live houses / clubs (2026-07-13 build-out) ---
    "unit_daikanyama":   (UnitScraper,                      ReviewStatus.PENDING),
    "club_citta":        (ClubCittaScraper,                 ReviewStatus.PENDING),
    "eggman":            (EggmanScraper,                    ReviewStatus.PENDING),
    "shibuya_dive":      (ShibuyaDiveScraper,               ReviewStatus.PENDING),
    "reny_shinjuku":     (RenyScraper,                      ReviewStatus.PENDING),
    "que_shimokitazawa": (QueScraper,                       ReviewStatus.PENDING),
    "yokohama_bay_hall": (BayHallScraper,                   ReviewStatus.PENDING),
    "fever_shindaita":   (FeverScraper,                     ReviewStatus.PENDING),
    "veats_shibuya":     (VeatsScraper,                     ReviewStatus.PENDING),
    "club_seata":        (SeataScraper,                     ReviewStatus.PENDING),
    "stellar_ball":      (StellarBallScraper,               ReviewStatus.PENDING),
    # --- jazz clubs (Blue Note Japan group) ---
    "bluenote_tokyo":    (BlueNoteTokyoScraper,             ReviewStatus.PENDING),
    "cotton_club":       (CottonClubScraper,                ReviewStatus.PENDING),
    # --- seated halls / theaters ---
    "ex_theater":        (ExTheaterScraper,                 ReviewStatus.PENDING),
    "line_cube_shibuya": (LineCubeShibuyaScraper,           ReviewStatus.PENDING),
    "hulic_hall":        (HulicHallScraper,                 ReviewStatus.PENDING),
    "kanadevia_hall":    (KanadeviaHallScraper,             ReviewStatus.PENDING),
    "sgc_hall_ariake":   (SgcHallScraper,                   ReviewStatus.PENDING),
    "tokyo_intl_forum":  (TokyoIntlForumScraper,            ReviewStatus.PENDING),
    "nhk_hall":          (NHKHallScraper,                   ReviewStatus.PENDING),
    "opera_city":        (OperaCityScraper,                 ReviewStatus.PENDING),
    "tachikawa_stage_garden": (TachikawaStageGardenScraper,
                               ReviewStatus.PENDING),
    "orchard_hall":      (OrchardHallScraper,               ReviewStatus.PENDING),
    # --- arenas / domes / stadiums ---
    "tokyo_dome":        (TokyoDomeScraper,                 ReviewStatus.PENDING),
    "tokyo_garden_theater": (GardenTheaterScraper,          ReviewStatus.PENDING),
    "ariake_arena":      (AriakeArenaScraper,               ReviewStatus.PENDING),
    "toyota_arena_tokyo": (ToyotaArenaScraper,              ReviewStatus.PENDING),
    "k_arena_yokohama":  (KArenaScraper,                    ReviewStatus.PENDING),
    "yoyogi_gym1":       (lambda: YoyogiScraper("yoyogi_gym1"),
                          ReviewStatus.PENDING),
    "kokuritsu_stadium": (KokuritsuStadiumScraper,          ReviewStatus.PENDING),
    "makuhari_messe":    (MakuhariMesseScraper,             ReviewStatus.PENDING),
    "yokohama_buntai":   (YokohamaBuntaiScraper,            ReviewStatus.PENDING),
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

            # Backlog drain: stored events still missing details (e.g. from
            # runs where the cap was hit, or listing-only runs) get enriched
            # too, even when today's listing parse reports them unchanged.
            if fetch_details and scraper.supports_detail:
                seen = {e.source_url for e in to_enrich}
                to_enrich.extend(store.events_needing_detail(
                    source_id, exclude_urls=seen,
                    limit=DETAIL_CAP - len(to_enrich)))

            detail_failures = 0
            for ev in to_enrich[:DETAIL_CAP]:
                try:
                    html = scraper.fetch(ev.source_url)
                    enriched = scraper.parse_detail(html, ev)
                    store.upsert(enriched, default_status)
                    report["details"] += 1
                except Exception:      # one bad detail page never kills a run
                    detail_failures += 1
                    continue
            # One flaky page is noise, but a fully-failed detail pass means
            # the venue's detail pages broke — that must be loud, not a
            # green source_health row that quietly stops backfilling.
            if detail_failures >= 3 and report["details"] == 0:
                report["error"] = (f"detail pass failed for all "
                                   f"{detail_failures} attempted pages")

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
