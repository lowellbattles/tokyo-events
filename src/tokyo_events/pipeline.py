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
import json
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
from .scrapers.sogo_tokyo import SogoTokyoScraper
from .scrapers.creativeman import CreativemanScraper
from .scrapers.smash import SmashScraper
from .scrapers.udo import UdoArtistsScraper
from .scrapers.disk_garage import DiskGarageScraper
from .scrapers.livenation import LiveNationScraper
from .scrapers.festivals import FestivalsScraper
from .scrapers.mori import MoriMuseumScraper
from .scrapers.museums import (TnmScraper, NactScraper,
                               ArtizonScraper, TobikanScraper, NmwaScraper,
                               NezuScraper, YamataneScraper, SompoScraper,
                               DesignSightScraper, MitsuiScraper,
                               PanasonicShiodomeScraper, TopMuseumScraper,
                               ShozokanScraper)
from .scrapers.galleries import OcagScraper, GggScraper
from .scrapers.matsuri import CuratedSeasonalScraper

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
    # --- promoters (their own productions; overlap with venue sources is
    #     folded at export by promoters.apply_promoter_merge) ---
    "sogo_tokyo":        (SogoTokyoScraper,                  ReviewStatus.PENDING),
    "creativeman":       (CreativemanScraper,                ReviewStatus.PENDING),
    "smash_jpn":         (SmashScraper,                      ReviewStatus.PENDING),
    "udo_artists":       (UdoArtistsScraper,                 ReviewStatus.PENDING),
    "disk_garage":       (DiskGarageScraper,                 ReviewStatus.PENDING),
    "livenation_jp":     (LiveNationScraper,                 ReviewStatus.PENDING),
    # --- festivals (curated editions; lineups scraped, dates are facts) ---
    "festivals":         (FestivalsScraper,                  ReviewStatus.PENDING),
    # --- museums / galleries (ART phase; category art, date-range events) --
    "mori_art_museum":   (lambda: MoriMuseumScraper("mori_art_museum"),
                          ReviewStatus.PENDING),
    "mori_arts_center_gallery":
        (lambda: MoriMuseumScraper("mori_arts_center_gallery"),
         ReviewStatus.PENDING),
    "tnm":               (TnmScraper,                       ReviewStatus.PENDING),
    # mot retired 2026-07-27: its WAF started 403ing GitHub-runner IPs
    # (works fine from residential IPs with our honest UA — rule 2, no
    # evasion). Scraper class + fixtures + venues/genres entries kept
    # for a revisit; stored events age out naturally. See CLAUDE.md.
    "nact":              (NactScraper,                      ReviewStatus.PENDING),
    "artizon":           (ArtizonScraper,                   ReviewStatus.PENDING),
    "tobikan":           (TobikanScraper,                   ReviewStatus.PENDING),
    "nmwa":              (NmwaScraper,                      ReviewStatus.PENDING),
    "nezu":              (NezuScraper,                      ReviewStatus.PENDING),
    "yamatane":          (YamataneScraper,                  ReviewStatus.PENDING),
    "sompo":             (SompoScraper,                     ReviewStatus.PENDING),
    "design_sight_2121": (DesignSightScraper,               ReviewStatus.PENDING),
    "mitsui":            (MitsuiScraper,                    ReviewStatus.PENDING),
    "panasonic_shiodome": (PanasonicShiodomeScraper,        ReviewStatus.PENDING),
    "top_museum":        (TopMuseumScraper,                 ReviewStatus.PENDING),
    "shozokan":          (ShozokanScraper,                  ReviewStatus.PENDING),
    # --- galleries / art spaces (ART phase gallery ring) ------------------
    "opera_city_gallery": (OcagScraper,                     ReviewStatus.PENDING),
    # what_museum retired 2026-07-27 with mot (same runner-IP WAF story).
    "ggg":               (GggScraper,                       ReviewStatus.PENDING),
    # --- seasonal curated (matsuri + fireworks; dates are facts) ----------
    "matsuri":           (lambda: CuratedSeasonalScraper("matsuri"),
                          ReviewStatus.PENDING),
    "hanabi":            (lambda: CuratedSeasonalScraper("hanabi"),
                          ReviewStatus.PENDING),
    "flowers":           (lambda: CuratedSeasonalScraper("flowers"),
                          ReviewStatus.PENDING),
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
                  "unchanged": 0, "details": 0, "error": None,
                  "skipped_venues": [], "stale_upcoming": []}
        started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        scraper: BaseScraper | None = None
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
                # Sold-out sweep: leftover budget re-checks the detail pages
                # of soon-upcoming shows — venues add 完売/SOLD OUT there
                # long after our one-time enrichment (parse_detail only
                # fills gaps / flips is_sold_out, so re-runs are safe).
                seen = {e.source_url for e in to_enrich}
                to_enrich.extend(store.events_for_soldout_sweep(
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

            # Seasonal sources (festivals) legitimately go quiet off-season;
            # they opt out of the loud-zero rule via allow_empty.
            if report["found"] == 0 and not getattr(scraper, "allow_empty",
                                                    False):
                report["error"] = ("0 events parsed — site structure may "
                                   "have changed")
        except Exception:
            report["error"] = traceback.format_exc(limit=3)

        # Venue strings a scraper saw but couldn't resolve to venues.CANONICAL
        # (promoter sources collect these) — surfaced even on a partial run so
        # curation gaps show up in the report/health JSON, not just live
        # sessions.
        if scraper is not None and getattr(scraper, "skipped_venues", None):
            report["skipped_venues"] = sorted(scraper.skipped_venues)

        store.conn.execute(
            "INSERT INTO scrape_runs (source, started_at, finished_at, found, "
            "new, changed, details_fetched, error, skipped_venues) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (source_id, started,
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             report["found"], report["new"], report["changed"],
             report["details"], report["error"],
             json.dumps(report["skipped_venues"], ensure_ascii=False)
             if report["skipped_venues"] else None))
        store.conn.commit()
        reports.append(report)

    # Stale-upcoming surfacing (R8): approved events still upcoming whose
    # last_seen predates the last few runs — the source stopped listing
    # them (possible cancellation) or they sit beyond its walk window.
    # Attached only to sources whose run JUST succeeded: a broken
    # scraper's events are stale for a reason the error report already
    # covers. Report-only; nothing is auto-hidden.
    by_src: dict[str, list[dict]] = {}
    for row in store.stale_upcoming():
        by_src.setdefault(row["source"], []).append(row)
    for rep in reports:
        if rep["error"] is None:
            rep["stale_upcoming"] = by_src.get(rep["source"], [])
    return reports
