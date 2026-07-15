"""Scraper for Live Nation Japan — https://www.livenationhip.co.jp

Live Nation Japan (formerly H.I.P. / Hayashi International Promotions,
now "Live Nation H.I.P.") is a concert PROMOTER — the production company
of record for many arena/hall shows it books nationwide — reached here
through its own official calendar, same role sogo_tokyo/creativeman/
smash_jpn/udo_artists play. Its own promoter identity is genuine (event
credits read "制作：… ／Live Nation H.I.P"); the page happens to be built
on Live Nation International's shared, Ticketmaster-family search platform
(a Next.js App Router SPA over an Umbraco headless CMS), which the owner
signed off on onboarding as a promoter source.

SCOPING — the one non-obvious thing. The site is a JS-only shell; the
listing page (/event/allevents) fires a single XHR:

    GET /api/search/events
        ?IncludePostponed=true&IncludeCancelled=true
        &Url=%2Fevent%2Fallevents&PageSize=<n>&Page=<n>

That endpoint is backed by ONE shared multi-territory index: called bare,
it returns a GLOBAL feed (≈9,200 events across ~55 countries, only ~1/100
Japan). The scope-to-Japan control is a querystring facet the frontend
applies from its country aggregation: ``CountryIds=110`` (110 == Japan's
countryId in the shared index). WITH it, the exact same endpoint returns
the Japan-only feed the site itself shows (``total`` ~80, every doc
``siteId`` 37 / ``venue.country`` "Japan") — from a plain, unauthenticated
GET with our normal UA, no cookies/impersonation, no special headers.
(The param is PascalCase-plural: ``siteId=``/``country=``/``CountryIds``'s
singular cousins all 400 as "not a valid querystring parameter", which is
what stumped earlier probes. ``Location=Tokyo`` / ``CityIds=<id>`` narrow
further, but we take the whole Japan feed and gate on venue instead.)

We still defensively drop any non-Japan document (``siteId`` != 37 or
``venue.country`` != "Japan") so a param regression can never publish a
Berlin show.

VENUE GATE (Kanto). ``venues.resolve_venue`` is the authoritative geo/
curation gate — the registry holds only in-scope Kanto venues, so a
resolve hit is always publishable (surfacing e.g. 日本武道館 / 東京ドーム,
which those venues' own sites don't list). For UNresolved venues we split
by ``venue.city``: a Kanto city (Tokyo/Kanagawa/Yokohama/Kawasaki/Saitama/
Chiba) is a real curation gap → collected in ``skipped_venues`` for the
operator to admit via venues.CANONICAL (this feed surfaces ベルーナドーム
and パシフィコ横浜, both Kanto arenas the project can't scrape directly);
a non-Kanto city (Osaka/Fukuoka/…) is simply out of scope → dropped
quietly. Venue strings are stored RAW; promoters.py folds duplicates into
the directly-scraped venue records at export.

FEED SHAPE (per-document facts, ``supports_detail = False`` — the listing
IS the detail; there are no per-event pages of ours to fetch):
- one document per city/date leg of a tour (a 4-city tour is 4 docs, each
  with its own id, venue, date and ``localizations[].url``) — no prose
  splitting needed, unlike what a single bundled record would require.
- DATE: ``eventDate`` is the local date stamped midnight-Z, so
  ``eventDate[:10]`` is the JST calendar date directly; ``eventDateTo``
  gives the last date of a multi-day run (== eventDate for single days).
- TITLE / LINEUP: ``lineup[]`` carries headline + support acts
  (e.g. The Weeknd + CREEPY NUTS). Title is the headline entry's name
  (a solo tour's headline name includes the tour, e.g. "超特急 BULLET
  TRAIN ARENA TOUR 2026 ESCORT"); lineup keeps every act's name for
  artist cross-referencing.
- SOLD OUT: ``allTicketStatus`` (and ``tickets[].ticketStatus``) == 3
  means sold out — verified against the site's own "チケットは完売しました"
  badges (status 1 == on sale). No structured OPEN/START times, seat-tier
  prices or playguide links live in this feed (they exist only as free
  prose we do not copy), so those fields stay empty by design.
- URL: the canonical event page is ``localizations[]`` (ja-JP) ``url`` on
  the sibling www.livenation.co.jp domain; used for both source_url
  (dedupe identity) and ticket_url.

Parsing keys off the ``/api/search/events`` JSON contract (the CountryIds
scope, siteId 37, the documents[] field), not display markup — a shape
change fails loud (ValueError / a page-1 empty-documents guard) rather
than silently publishing nothing.
"""

from __future__ import annotations

import json
from typing import Iterable

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper


class LiveNationScraper(BaseScraper):
    source_id = "livenation_jp"
    source_name = "Live Nation Japan"
    BASE = "https://www.livenationhip.co.jp"
    supports_detail = False        # the search feed is already complete
    rate_limit_s = 2.0

    #: countryId that scopes the shared global index to Japan (the whole
    #: reason this source is scrapeable politely — see module docstring).
    JP_COUNTRY_ID = 110
    #: Japan-market siteId in the returned documents (defensive JP guard).
    JP_SITE_ID = 37
    #: allTicketStatus / tickets[].ticketStatus value that means sold out.
    SOLD_OUT_STATUS = 3
    #: venue.city strings we treat as in-scope Kanto. Everything else is
    #: out of region and dropped quietly (never logged as a curation gap).
    KANTO_CITIES = frozenset(
        {"tokyo", "yokohama", "kawasaki", "kanagawa", "saitama", "chiba"})

    def __init__(self, page_size: int = 100, max_pages: int = 10, **kw):
        super().__init__(**kw)
        self.page_size = page_size
        self.max_pages = max_pages
        #: raw venue strings resolve_venue() couldn't place but whose city
        #: is Kanto — distinct, accumulated for operator visibility (extend
        #: venues.CANONICAL/_EXTRA_ALIASES to admit them).
        self.skipped_venues: set[str] = set()

    # ---------------------------------------------------------------- fetch
    def _api_url(self, page: int) -> str:
        return (f"{self.BASE}/api/search/events"
                f"?IncludePostponed=true&IncludeCancelled=true"
                f"&Url=%2Fevent%2Fallevents"
                f"&PageSize={self.page_size}&Page={page}"
                f"&CountryIds={self.JP_COUNTRY_ID}")

    def scrape(self) -> Iterable[Event]:
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            data = self._load(self.fetch(self._api_url(page)))
            docs = data.get("documents") or []
            # Structural failure must be loud: Kanto always has Live Nation
            # shows, so an empty first page means the endpoint or the
            # CountryIds scoping broke — not a quiet zero-row run.
            if page == 1 and not docs:
                raise RuntimeError(
                    f"[{self.source_id}] /api/search/events returned 0 "
                    f"documents — endpoint or Japan scoping "
                    f"(CountryIds={self.JP_COUNTRY_ID}) changed")
            for ev in self._collect(docs):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev
            total = data.get("total")
            if (len(docs) < self.page_size
                    or (isinstance(total, int) and page * self.page_size >= total)):
                break

    # ------------------------------------------------------------ pure parse
    def parse(self, raw: str, **context) -> list[Event]:
        """Pure parse of one /api/search/events JSON page → list[Event].
        Malformed JSON or a response with no ``documents`` field raises
        (loud), matching this project's 'structural failure is loud' rule;
        a valid-but-empty documents list yields no events."""
        data = self._load(raw)
        return self._collect(data.get("documents") or [])

    def _load(self, raw: str) -> dict:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(
                f"[{self.source_id}] response is not valid JSON") from e
        if not isinstance(data, dict) or "documents" not in data:
            raise ValueError(
                f"[{self.source_id}] response missing 'documents' — the "
                f"/api/search/events contract changed")
        return data

    def _collect(self, docs: list) -> list[Event]:
        out: list[Event] = []
        seen: set[str] = set()
        for d in docs:
            ev = self._parse_doc(d)
            if ev and ev.source_url not in seen:
                seen.add(ev.source_url)
                out.append(ev)
        return out

    def _parse_doc(self, d: dict) -> Event | None:
        if not isinstance(d, dict):
            return None
        venue = d.get("venue") or {}

        # (1) Japan guard. The feed is CountryIds-scoped, but never emit a
        # non-Japan row if that ever regresses — siteId 37 is the Japan
        # market, venue.country the human-readable cross-check.
        site_id = d.get("siteId")
        country = venue.get("country")
        if site_id is not None and site_id != self.JP_SITE_ID:
            return None
        if country and country != "Japan":
            return None

        vname = (venue.get("name") or "").strip()
        if not vname:
            return None

        # (2) Kanto gate. resolve_venue is authoritative (registry = Kanto
        # venues only); an unresolved Kanto-city venue is a curation gap to
        # surface, a non-Kanto city is simply out of scope.
        key = resolve_venue(vname)
        if key is None:
            if self._is_kanto(venue.get("city")):
                self.skipped_venues.add(vname)
            return None

        start = (d.get("eventDate") or "")[:10]
        if len(start) != 10:
            return None
        end = (d.get("eventDateTo") or "")[:10]
        end = end if (len(end) == 10 and end != start) else None

        url = self._event_url(d)
        if not url:
            return None

        title, lineup = self._title_and_lineup(d)
        if not title:
            return None

        sold_out = (d.get("allTicketStatus") == self.SOLD_OUT_STATUS
                    or any(t.get("ticketStatus") == self.SOLD_OUT_STATUS
                           for t in (d.get("tickets") or [])))

        category = (Category.OTHER if tu.is_nonmusic(title)
                    else Category.MUSIC)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category,
            start_date=start, end_date=end,
            venue_name=vname, venue_area=None, address=None,
            lat=None, lng=None,
            lineup=lineup, is_sold_out=sold_out,
            ticket_url=url,
        )

    # --------------------------------------------------------------- helpers
    def _is_kanto(self, city: str | None) -> bool:
        """True for in-scope Kanto cities (and for a missing city, so an
        unplaceable venue is surfaced rather than silently vanishing)."""
        if not city:
            return True
        return city.strip().casefold() in self.KANTO_CITIES

    @staticmethod
    def _event_url(d: dict) -> str | None:
        """Canonical event page: the ja-JP localization URL (on the sibling
        www.livenation.co.jp domain), falling back to any localization URL.
        This is the stable per-document dedupe identity."""
        locs = d.get("localizations") or []
        ja = next((l.get("url") for l in locs
                   if l.get("cultureName") == "ja-JP" and l.get("url")), None)
        if ja:
            return ja
        return next((l.get("url") for l in locs if l.get("url")), None)

    @staticmethod
    def _title_and_lineup(d: dict) -> tuple[str | None, list[str]]:
        """Title = the headline act's name (a solo tour's headline name
        carries the tour text); lineup = every act's name, deduped, for
        artist cross-referencing."""
        entries = d.get("lineup") or []
        names: list[str] = []
        for l in entries:
            nm = (l.get("name") or "").strip()
            if nm and nm not in names:
                names.append(nm)
        headline = next(
            ((l.get("name") or "").strip() for l in entries
             if (l.get("type") == "headline" or l.get("isPrimary"))
             and (l.get("name") or "").strip()),
            None)
        if not headline:
            headline = names[0] if names else None
        if not headline:
            locs = d.get("localizations") or []
            headline = next(
                (l.get("name") for l in locs
                 if l.get("cultureName") == "ja-JP" and l.get("name")),
                None) or (d.get("encodedName") or None)
        return headline, names
