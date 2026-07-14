"""FESTIVALS — a curated source class (roadmap item 5).

Architecturally different from venue scrapers. A festival's DATES, VENUE and
TICKET URL are *curated facts*: they are announced once per edition and do not
drift, so they live hard-coded in ``ACTIVE_EDITIONS`` below. Only the LINEUP is
scraped, with a small per-festival pure extractor keyed in the edition config.

Curated facts NEVER depend on parsing success. Every active edition yields its
skeleton events (dates + title + venue + ticket link, ``lineup=[]``) even when
its lineup page hasn't been announced yet, its extractor breaks, or the fetch
fails — the scrape wraps each edition's fetch/parse in try/except so one broken
festival can never take down the others (and a seasonal source with no active
edition must not trip the pipeline's loud-zero guard, hence ``allow_empty``).

Two lineup shapes:
- day_split=True  -> one Event per show day (``dates`` are the show days, in
  published order). ``start_date`` is that day; ``source_url`` is the edition
  URL (or a per-day page) + "#" + the ISO date; ``lineup`` is THAT day's acts.
- day_split=False -> ONE Event spanning ``dates[0]``..``dates[-1]`` with the
  full flat lineup.

Extractors are pure ``extract(payload, edition, day=None)`` functions returning
either ``dict[iso_date -> list[str]]`` (single payload carrying every day, e.g.
Fuji Rock's grid / RIJ's JSON) or ``list[str]`` (this payload's acts, e.g. one
Summer Sonic day page, or a non-split flat roster). They fail TOWARD an empty
lineup, never toward garbage names.

SUNSET RULE (for future maintainers): ``scrape()`` ignores any edition whose
run has already finished (``max(dates) < today``). When an edition is over,
move it to ``DORMANT_EDITIONS`` (which documents each site's parse pattern for
next season's re-curation) and add next year's edition here once its dates and
lineup URL are announced — the edition subdomain / year-folder usually changes
(2026.sweetloveshower.com -> 2027…, rijfes.jp/2026/ -> /2027/, …).

Facts only (Hard Rule 1): titles, dates, venue, lineup, ticket link, source
URL. Never copy poster imagery or prose descriptions — link out.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from bs4 import BeautifulSoup

from ..models import Category, Event
from ..venues import display_of
from .base import BaseScraper


# --------------------------------------------------------------------------- #
#  Name cleanup                                                               #
# --------------------------------------------------------------------------- #
def _clean_names(raw: Iterable[str]) -> list[str]:
    """Light cleanup for scraped act names: fold NBSP / full-width space,
    collapse whitespace runs (a name split across <br> becomes one line),
    drop empties and obvious non-names, and de-duplicate WITHIN the list
    (an act listed twice on the same day is noise) while preserving the
    published order. Caps nothing — full lineups feed the artist graph."""
    out: list[str] = []
    seen: set[str] = set()
    for name in raw:
        # Decode leftover HTML entities (lxml passes some HTML5 named entities
        # through undecoded, e.g. &Amacr; -> Ā), fold NBSP, collapse whitespace.
        s = html.unescape((name or "").replace("\xa0", " "))
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        # Bare stage / section labels sometimes leak into a name column; a
        # real act name is never one of these on its own.
        if s.upper() in _NON_NAME_LABELS:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


_NON_NAME_LABELS = {"TBA", "TBD", "COMING SOON", "AND MORE", "＆MORE", "& MORE"}


# --------------------------------------------------------------------------- #
#  Per-festival lineup extractors (pure — fixture-testable offline)           #
# --------------------------------------------------------------------------- #
def extract_fuji_rock(html: str, edition: "Edition", day: Optional[str] = None
                      ) -> dict[str, list[str]]:
    """Fuji Rock /artist/index — one <table class="artistlist"> per stage;
    inside each, 3-column rows map to day1/day2/day3 BY POSITION. The day
    column-header images carry stale alt text (25/26/27) that must be ignored —
    column index -> edition.dates[index] is the source of truth (see verdict).
    Long names split by <br>, and feat. credits in <span class="ts-smaller">,
    collapse into one line via get_text."""
    soup = BeautifulSoup(html, "lxml")
    dates = edition.dates
    daymap: dict[str, list[str]] = {d: [] for d in dates}
    for table in soup.select("table.artistlist"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            # stage-header rows are a single colspan td.stage — skip them.
            if len(tds) == 1 and "stage" in (tds[0].get("class") or []):
                continue
            for idx, td in enumerate(tds):
                if idx >= len(dates):
                    break
                for a in td.select("a.pop_detail"):
                    daymap[dates[idx]].append(a.get_text(" ", strip=True))
    return {d: _clean_names(names) for d, names in daymap.items()}


def extract_summer_sonic(html: str, edition: "Edition", day: Optional[str] = None
                         ) -> list[str]:
    """One Summer Sonic day page (/en/lineup/tokyo-dayN/). Acts are the
    <p class="name"> nodes inside each <div class="stageWrap"> stage block.
    Returns this page's flat list; scrape() assigns it to ``day``."""
    soup = BeautifulSoup(html, "lxml")
    names = [p.get_text(" ", strip=True)
             for p in soup.select("div.stageWrap p.name")]
    return _clean_names(names)


def extract_rock_in_japan(payload: str, edition: "Edition",
                          day: Optional[str] = None) -> dict[str, list[str]]:
    """RIJ /2026/api/get/artist/ JSON: {"contents": [{name, dates:[MMDD], …}]}.
    ``dates`` are bare MMDD codes with no year; we map them onto the edition's
    ISO show days. One act can appear on several dates."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {d: [] for d in edition.dates}
    rows = data.get("contents") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {d: [] for d in edition.dates}
    code_to_iso = {iso[5:7] + iso[8:10]: iso for iso in edition.dates}
    daymap: dict[str, list[str]] = {d: [] for d in edition.dates}
    for row in rows:
        if not isinstance(row, dict) or not row.get("display", True):
            continue
        name = (row.get("name") or "").strip()
        if not name:
            continue
        for code in row.get("dates", []) or []:
            iso = code_to_iso.get(str(code))
            if iso:
                daymap[iso].append(name)
    return {d: _clean_names(names) for d, names in daymap.items()}


def extract_sweet_love_shower(html: str, edition: "Edition",
                              day: Optional[str] = None) -> list[str]:
    """SWEET LOVE SHOWER /contents/artist/lineup — flat roster, one
    <h4 class="c-artistItem__name"> per act, no per-day attribution (the
    day-by-day timetable exists only as PDFs). Non-split single Event."""
    soup = BeautifulSoup(html, "lxml")
    names = [h.get_text(" ", strip=True)
             for h in soup.select("h4.c-artistItem__name")]
    return _clean_names(names)


def extract_ultra_japan(html: str, edition: "Edition",
                        day: Optional[str] = None) -> list[str]:
    """ULTRA JAPAN /lineup — the lineup is delivered ONLY as a poster/flyer
    PNG (Phase-2 wave), never as text or markup. There is nothing text-parseable
    to extract, so this returns [] and the skeleton event stands with lineup=[]
    (copying the poster would violate Hard Rule 1 anyway). If a future edition
    ever publishes a text list, this is where to parse it."""
    return []


# --------------------------------------------------------------------------- #
#  Edition config                                                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Edition:
    key: str                              # festival key (== venues.py CANONICAL key)
    title_ja: str                         # official edition title
    venue_area: str                       # human location ("Naeba, Niigata")
    day_split: bool
    dates: tuple[str, ...]                # ISO show days, in published order
    edition_url: str                      # canonical page (source_url base)
    ticket_url: Optional[str] = None
    genres: tuple[str, ...] = ()          # fixed prior (e.g. ("electronic",))
    #: (day_iso_or_None, fetch_url) targets to GET for the lineup. Empty tuple
    #: => skeleton-only (nothing announced yet); NEVER points at a stale edition.
    lineup_targets: tuple[tuple[Optional[str], str], ...] = ()
    #: iso -> per-day source URL, when day pages are distinct (Summer Sonic).
    #: When None, day_split events use edition_url + "#" + iso.
    day_urls: Optional[dict] = None
    extractor: Optional[Callable] = None

    @property
    def venue_name(self) -> str:
        # The festival itself is the venue identity (venues.py, vclass festival).
        return display_of(self.key) or self.key

    def end_of_run(self) -> str:
        return max(self.dates)

    def source_url_for(self, iso: Optional[str]) -> str:
        if not self.day_split or iso is None:
            return self.edition_url
        if self.day_urls and iso in self.day_urls:
            return self.day_urls[iso]
        return f"{self.edition_url}#{iso}"


#: Active 2026 editions. Move finished ones to DORMANT_EDITIONS after their run.
ACTIVE_EDITIONS: tuple[Edition, ...] = (
    Edition(
        key="fuji_rock",
        title_ja="FUJI ROCK FESTIVAL '26",
        venue_area="Naeba, Niigata",
        day_split=True,
        dates=("2026-07-24", "2026-07-25", "2026-07-26"),
        edition_url="https://www.fujirockfestival.com/artist/index",
        ticket_url="https://www.fujirockfestival.com/ticket/index",
        lineup_targets=((None, "https://www.fujirockfestival.com/artist/index"),),
        extractor=extract_fuji_rock,
    ),
    Edition(
        key="summer_sonic_tokyo",
        title_ja="SUMMER SONIC 2026",
        venue_area="Makuhari, Chiba",
        day_split=True,
        dates=("2026-08-14", "2026-08-15", "2026-08-16"),
        edition_url="https://www.summersonic.com/en/lineup/",
        ticket_url="https://www.summersonic.com/en/tickets/tokyo/",
        lineup_targets=(
            ("2026-08-14", "https://www.summersonic.com/en/lineup/tokyo-day1/"),
            ("2026-08-15", "https://www.summersonic.com/en/lineup/tokyo-day2/"),
            ("2026-08-16", "https://www.summersonic.com/en/lineup/tokyo-day3/"),
        ),
        day_urls={
            "2026-08-14": "https://www.summersonic.com/en/lineup/tokyo-day1/",
            "2026-08-15": "https://www.summersonic.com/en/lineup/tokyo-day2/",
            "2026-08-16": "https://www.summersonic.com/en/lineup/tokyo-day3/",
        },
        extractor=extract_summer_sonic,
    ),
    Edition(
        key="rock_in_japan",
        title_ja="ROCK IN JAPAN FESTIVAL 2026",
        venue_area="Soga, Chiba",
        day_split=True,
        # Two non-contiguous weekend blocks (5 show days total).
        dates=("2026-09-12", "2026-09-13",
               "2026-09-19", "2026-09-20", "2026-09-21"),
        edition_url="https://rijfes.jp/2026/",
        ticket_url="https://rijfes.jp/2026/ticket/archive/",
        lineup_targets=((None, "https://rijfes.jp/2026/api/get/artist/"),),
        extractor=extract_rock_in_japan,
    ),
    Edition(
        key="sweet_love_shower",
        title_ja="SWEET LOVE SHOWER 2026",
        venue_area="Lake Yamanakako, Yamanashi",
        day_split=False,          # roster has no per-day split -> one multi-day Event
        dates=("2026-08-28", "2026-08-29", "2026-08-30"),
        edition_url="https://2026.sweetloveshower.com/contents/artist/lineup",
        ticket_url="https://2026.sweetloveshower.com/contents/ticket/admission",
        lineup_targets=(
            (None, "https://2026.sweetloveshower.com/contents/artist/lineup"),),
        extractor=extract_sweet_love_shower,
    ),
    Edition(
        key="ultra_japan",
        title_ja="ULTRA JAPAN 2026",
        venue_area="Odaiba, Tokyo",
        day_split=False,
        dates=("2026-09-19", "2026-09-20"),
        edition_url="https://ultrajapan.com/lineup",
        ticket_url="https://ultrajapan.com/tickets-2026",
        genres=("electronic",),   # fixed prior — no per-act tagging needed
        # Lineup is a poster image only (Phase 2); extractor returns [] and the
        # skeleton stands. Still fetched so a future text list would be picked up.
        lineup_targets=((None, "https://ultrajapan.com/lineup"),),
        extractor=extract_ultra_japan,
    ),
    Edition(
        key="countdown_japan",
        title_ja="COUNTDOWN JAPAN 26/27",
        venue_area="Makuhari, Chiba",
        day_split=True,
        # 12/28 is a DARK day (no show) — deliberately absent from the run.
        dates=("2026-12-26", "2026-12-27",
               "2026-12-29", "2026-12-30", "2026-12-31"),
        edition_url="https://countdownjapan.jp/",
        ticket_url=None,          # 26/27 ticketing not announced yet
        # lineup_targets EMPTY on purpose: the 26/27 lineup is unannounced and
        # the site still serves the FINISHED 25/26 roster under /2526/. We must
        # NEVER ingest that stale lineup — yield pure skeletons until the 26/27
        # /api/get/artist/ endpoint (directory 2627) goes live next season.
        lineup_targets=(),
        extractor=None,
    ),
)


#: Editions whose 2026 run is already over. Kept as documentation so next
#: season's curation can restore them quickly — each entry records the parse
#: pattern distilled from its probe verdict. When re-curating: bump the year
#: folder / subdomain, re-verify robots.txt + markup, move back into
#: ACTIVE_EDITIONS with fresh dates and a lineup_targets URL.
DORMANT_EDITIONS: tuple[dict, ...] = (
    {
        "key": "japan_jam",
        "title": "JAPAN JAM",
        "venue_area": "Soga, Chiba",
        "day_split": True,
        "last_run": "2026-05-02..2026-05-05",
        "lineup_url": "https://japanjam.jp/{year}/api/get/artist/",
        "parse_pattern": "rockin'on-family JSON API (same shape as RIJ / CDJ): "
                         "GET /{year}/api/get/artist/ -> {contents:[{name, "
                         "dates:[MMDD], display, ...}]}. Year folder in the URL; "
                         "map MMDD codes onto the edition's ISO dates. Reuse "
                         "extract_rock_in_japan almost verbatim.",
    },
    {
        "key": "metrock_tokyo",
        "title": "METROCK (TOKYO)",
        "venue_area": "Umi-no-Mori Park, Koto-ku, Tokyo",
        "day_split": True,
        "last_run": "2026-05-16..2026-05-17",
        "lineup_url": "https://metrock.jp/artist/lineup/index.html",
        "parse_pattern": "Static HTML. Filter to div.place-container.tokyo (the "
                         "page also carries the Osaka leg — out of scope), then "
                         "per div.date-container (#date16/#date17) read "
                         "<p class='name'> acts. DROP '【…DJ】' interstitials. "
                         "Dates/venue/price on /ticket/index.html.",
    },
    {
        "key": "viva_la_rock",
        "title": "VIVA LA ROCK",
        "venue_area": "Saitama Stadium 2002 grounds, Saitama",
        "day_split": True,
        "last_run": "2026-05-03..2026-05-06",
        "lineup_url": "https://vivalarock.jp/{year}/artist/lineup.html",
        "parse_pattern": "Static HTML, durable /{year}/artist/lineup.html URL. "
                         "One <div class='dateBox'> per day (in day order); acts "
                         "are the SECOND <p> inside each <div class='box'> (img "
                         "alt is empty). Dates in meta description prose. No set "
                         "times (timetable is image/PDF).",
    },
    {
        "key": "synchronicity_fes",
        "title": "SYNCHRONICITY",
        "venue_area": "Shibuya, Tokyo (Spotify O-EAST + O circuit)",
        "day_split": True,
        "last_run": "2026-04-11..2026-04-12",
        "lineup_url": "https://synchronicity.tv/festival/artists/",
        "parse_pattern": "Static WordPress, STABLE (non-year) slug that is "
                         "overwritten each edition — read the year from image "
                         "upload paths, not the URL, and guard against serving "
                         "last year's cached HTML. Acts: img alt= inside "
                         "div.loop-block; day via 'category-4NN-fri/sat/sun' "
                         "class tokens.",
    },
    {
        "key": "greenroom_fes",
        "title": "GREENROOM FESTIVAL",
        "venue_area": "Yokohama Red Brick Warehouse, Yokohama",
        "day_split": True,
        "last_run": "2026-05-23..2026-05-24",
        "lineup_url": "https://greenroom.jp/wp-json/wp/v2/artist?per_page=100",
        "parse_pattern": "WordPress REST API: each item has title.rendered "
                         "(act name), link, and appearance_date / class_list "
                         "carrying 'appearance_date-day1|day2'. Map day1/day2 "
                         "taxonomy to the real dates (from the homepage). "
                         "Homepage #t_lineup is an equivalent static fallback.",
    },
)


# --------------------------------------------------------------------------- #
#  Scraper                                                                    #
# --------------------------------------------------------------------------- #
class FestivalsScraper(BaseScraper):
    source_id = "festivals"
    source_name = "Festivals (curated)"
    #: Seasonal: legitimately yields nothing off-season — opt out of the
    #: pipeline's loud-zero guard (pipeline honors getattr(scraper, allow_empty)).
    allow_empty = True
    #: Facts are curated + lineups come from the listing pass; no per-event
    #: detail pages to fetch.
    supports_detail = False

    def scrape(self, today: Optional[str] = None) -> Iterable[Event]:
        today = today or dt.date.today().isoformat()
        for ed in ACTIVE_EDITIONS:
            if ed.end_of_run() < today:      # sunset: run already finished
                continue
            payloads: dict[str, str] = {}
            if ed.lineup_targets:
                try:
                    for _day, url in ed.lineup_targets:
                        if url not in payloads:
                            payloads[url] = self.fetch(url)
                except Exception:
                    # A broken/stale fetch never sinks the edition — the
                    # curated skeleton events still ship.
                    payloads = {}
            yield from self.build_edition(ed, payloads, today=today)

    def build_edition(self, ed: Edition, payloads: dict[str, str],
                      today: Optional[str] = None) -> list[Event]:
        """Pure: build one edition's skeleton events from curated facts, then
        merge in any lineups extractable from ``payloads`` (a {url: text} map).
        Given empty/garbage payloads, returns the skeletons with lineup=[].
        Fully offline-testable — scrape() only adds the fetching around this."""
        skeletons = self._skeletons(ed)          # ordered {key -> Event}
        daymap: dict[Optional[str], list[str]] = {}
        if ed.extractor and ed.lineup_targets:
            for day, url in ed.lineup_targets:
                payload = payloads.get(url)
                if payload is None:
                    continue
                try:
                    res = ed.extractor(payload, ed, day)
                except Exception:
                    res = {} if ed.day_split else []
                if isinstance(res, dict):
                    for iso, names in res.items():
                        daymap.setdefault(iso, []).extend(names)
                else:
                    daymap.setdefault(day, []).extend(res)

        for key, ev in skeletons.items():
            ev.lineup = _clean_names(daymap.get(key, []))
        return list(skeletons.values())

    def _skeletons(self, ed: Edition) -> dict[Optional[str], Event]:
        """Curated skeleton events (dates + title + venue + ticket link,
        lineup=[]). day_split -> one per show day keyed by ISO date; otherwise
        a single multi-day Event keyed by None."""
        out: dict[Optional[str], Event] = {}
        if ed.day_split:
            for iso in ed.dates:
                out[iso] = Event(
                    source=self.source_id,
                    source_url=ed.source_url_for(iso),
                    title_ja=ed.title_ja,
                    category=Category.MUSIC_FESTIVAL,
                    genres=list(ed.genres),
                    start_date=iso, end_date=None,
                    venue_name=ed.venue_name, venue_area=ed.venue_area,
                    ticket_url=ed.ticket_url, lineup=[],
                )
        else:
            out[None] = Event(
                source=self.source_id,
                source_url=ed.source_url_for(None),
                title_ja=ed.title_ja,
                category=Category.MUSIC_FESTIVAL,
                genres=list(ed.genres),
                start_date=ed.dates[0], end_date=ed.dates[-1],
                venue_name=ed.venue_name, venue_area=ed.venue_area,
                ticket_url=ed.ticket_url, lineup=[],
            )
        return out

    def parse(self, payload: str, **context) -> list[Event]:
        """Unused by the pipeline: festivals do not have a single generic
        listing parse — each edition carries its own pure lineup extractor
        (see build_edition / Edition.extractor). Present only to satisfy the
        BaseScraper interface."""
        return []
