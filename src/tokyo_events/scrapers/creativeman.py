"""Scraper for CREATIVEMAN — https://www.creativeman.co.jp

CREATIVEMAN is a concert PROMOTER, not a venue: a single organizer that
books shows (strong international roster) into many different halls. So
this is a new source class — a promoter calendar — and it deviates
DELIBERATELY from the usual two-stage (listing + per-event detail) pattern:

  * LISTING: month pages at /event/?cmy=YYYY&cmm=M render a static
    WordPress calendar grid. Each day cell (td.p-calendar__cell, with the
    day number in .p-calendar__label) holds zero-or-more
    div.p-calendar__live blocks. Each block is one show: an <a> to the
    tour page, a .p-calendar__areaLabel--<pref> chip giving the prefecture
    at LISTING level, the artist text, and an optional status badge
    (SOLD OUT / 発売中 / 当日券あり …). We keep only KANTO prefecture
    chips (tokyo/kanagawa/yokohama/chiba/saitama).

  * TOUR PAGES are ONE PER TOUR, not per date: every venue/date leg is a
    separate <table> under the "TICKET INFORMATION" heading. Each table's
    header cell is "<pref-kanji> YYYY/M/D(weekday) <venue>" (+ a SOLD OUT
    label), followed by th/td rows: ゲスト・Support Act (guests),
    開場・開演 (OPEN/START), チケット (price tiers), プレイガイド (playguide
    links). Because a page is shared across many calendar dates, we fetch
    each distinct Kanto tour URL ONCE per run (per-run dict cache) and
    yield one Event PER KANTO LEG. There is no separate per-event page, so
    supports_detail = False (all enrichment happens inside scrape).

Politeness: tour-page fetches go through self.fetch (rate limiter applies)
and are capped at tour_fetch_cap per run, fetched in date order. When the
cap is hit, the remaining calendar rows are still yielded WITHOUT leg
detail (date + artist + prefecture only, title from the artist) so the
pipeline stages them and a later run enriches them — the deferred row's
source_url ("<tour>#<ISO date>") matches the leg's source_url on the next
run, so the upsert fills in venue/times/price rather than duplicating.

Venue curation: a leg's raw venue string is resolved with venues.resolve_
venue at scrape time; legs that don't resolve are SKIPPED (the prefecture
filter already ran at listing level, so this second filter catches
un-curated Kanto halls). Skipped Kanto venue strings are collected on the
instance (self.skipped_venues) for the integrator to add to venues.py.
The RAW venue string is stored on kept Events — canonical resolution
happens again at export.

Parsers key off URL/text conventions (the /event/ slug, the YYYY/M/D leg
header, the Japanese row labels) rather than CSS class names, so a
structural break yields zero events (loud), never silent garbage.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from typing import Iterable, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from ..venues import resolve_venue
from .base import BaseScraper
from . import textutils as tu

BASE = "https://www.creativeman.co.jp"
EVENT_BASE = f"{BASE}/event/"

#: areaLabel--<suffix> classes we keep at listing level (Kanto scope).
KANTO_CLASSES = {"tokyo", "kanagawa", "yokohama", "chiba", "saitama"}
#: leg-header prefecture kanji considered in-scope Kanto — used ONLY to
#: decide whether an unresolved venue is worth reporting (a national tour's
#: Osaka/Aichi legs also fail to resolve, but we don't want them in the
#: "un-curated Kanto hall" report).
KANTO_PREF_KANJI = {"東京", "神奈川", "千葉", "埼玉", "茨城", "栃木", "群馬", "横浜"}

AREA_RE = re.compile(r"p-calendar__areaLabel--(\w+)")
# Absolute YYYY/M/D inside a leg header (weekday paren follows, e.g.
# "東京 2026/7/15(水) Zepp Shinjuku" or "... 2026/7/19 (日) ...").
LEG_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_WEEKDAY_PAREN_RE = re.compile(r"^\s*[（(][^）)]*[)）]\s*")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


# ---------------------------------------------------------------- tour parse
def parse_tour(html: str, tour_url: str | None = None,
               **context) -> dict:
    """Pure parse of a tour page into {title, artist, legs}. No network.

    `legs` is a list of dicts: pref (kanji), date (ISO), venue (raw string),
    open_time, start_time, price_text, price_min, is_free, ticket_links,
    guests, sold_out.  A leg is any <table> whose header cell carries an
    absolute YYYY/M/D — that keys off the site's text convention, not a CSS
    class, so a structure change yields zero legs (loud).
    """
    soup = BeautifulSoup(html, "lxml")

    # Tour/event title (the headline on the page) and the plain artist name.
    h1 = soup.select_one("h1.p-jumbotron__title") or soup.find("h1")
    page_title = _clean(h1.get_text(" ", strip=True)) if h1 else None
    artist = None
    if soup.title:
        # "<artist> - CREATIVEMAN PRODUCTIONS"
        artist = re.split(r"\s*[-|｜–]\s*CREATIVEMAN",
                          soup.title.get_text(strip=True))[0].strip() or None

    legs: list[dict] = []
    for table in soup.find_all("table"):
        hdr = None
        for th in table.find_all("th"):
            if LEG_DATE_RE.search(th.get_text(" ", strip=True)):
                hdr = th
                break
        if hdr is None:
            continue
        leg = _parse_leg(table, hdr)
        if leg:
            legs.append(leg)
    return {"title": page_title, "artist": artist, "legs": legs}


def _parse_leg(table, hdr) -> dict | None:
    # Clean leg header: the u-text-large span carries it without the SOLD OUT
    # label; fall back to the whole cell.
    span = hdr.select_one("span.u-text-large")
    header_text = _clean((span or hdr).get_text(" ", strip=True))
    m = LEG_DATE_RE.search(header_text)
    if not m:
        return None
    try:
        date = dt.date(int(m.group(1)), int(m.group(2)),
                       int(m.group(3))).isoformat()
    except ValueError:
        return None
    pref = header_text[:m.start()].strip()
    venue = _clean(_WEEKDAY_PAREN_RE.sub("", header_text[m.end():]))
    if not venue:
        return None

    sold_out = bool(hdr.select_one(".event-label__soldout")) or \
        bool(tu.SOLD_OUT_RE.search(hdr.get_text(" ", strip=True)))

    open_time = start_time = None
    price_text = price_min = is_free = None
    ticket_links: list[dict] = []
    guests: list[str] = []
    for tr in table.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if not (th and td):
            continue                       # header row (colspan) or spacer
        label = _clean(th.get_text(" ", strip=True))
        cell_text = td.get_text(" ", strip=True)
        if "開場" in label or "開演" in label:
            open_time, start_time = tu.parse_times(cell_text)
        elif label == "チケット":          # price row only (not 発売日 / 先行)
            price_text, price_min, is_free = tu.parse_prices(
                tu.strip_drink_charges(cell_text))
        elif "プレイガイド" in label:
            ticket_links = tu.extract_ticket_links(td, cell_text)
        elif "ゲスト" in label or "support act" in label.lower():
            guests = [g.strip() for g in re.split(r"[/／、,]", cell_text)
                      if g.strip()]

    return {
        "pref": pref, "date": date, "venue": venue,
        "open_time": open_time, "start_time": start_time,
        "price_text": price_text, "price_min": price_min, "is_free": is_free,
        "ticket_links": ticket_links, "guests": guests, "sold_out": sold_out,
    }


# --------------------------------------------------------------------- class
class CreativemanScraper(BaseScraper):
    source_id = "creativeman"
    source_name = "CREATIVEMAN"
    rate_limit_s = 2.0
    #: all enrichment happens inside scrape(); there is no per-event page.
    supports_detail = False

    def __init__(self, months_ahead: int = 3, tour_fetch_cap: int = 25, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        self.tour_fetch_cap = tour_fetch_cap
        #: raw venue strings of Kanto legs we could not resolve (report only).
        self.skipped_venues: set[str] = set()

    # ------------------------------------------------------------- fetching
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        rows: list[Event] = []
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{EVENT_BASE}?cmy={m.year}&cmm={m.month}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise                  # the current month must be reachable
                break                      # far-future month not published yet
            rows.extend(self.parse(html, month=m))
        yield from self._process(rows, floor_date=first.isoformat())

    def _process(self, rows: list[Event],
                 floor_date: str | None = None) -> Iterator[Event]:
        """Group listing rows by their shared tour URL, fetch each distinct
        Kanto tour page once (in date order, up to tour_fetch_cap) and yield
        one Event per kept Kanto leg; rows beyond the cap are deferred. Split
        out from scrape() so the cap/grouping logic is unit-testable without
        depending on the wall clock.

        `floor_date` (ISO) drops enriched legs that fall before the listing
        window — a tour page lists ALL its legs, including ones in an already-
        passed month that our forward month-walk never showed. Deferred rows
        come straight from the listing, so they are always in-window."""
        # Group calendar rows by their (shared) tour URL, ordered by the
        # tour's earliest Kanto date so the fetch budget covers the soonest
        # shows first.
        tours: dict[str, list[Event]] = {}
        for r in rows:
            tours.setdefault(r.source_url, []).append(r)
        ordered = sorted(
            tours.items(),
            key=lambda kv: min(e.start_date for e in kv[1] if e.start_date))

        cache: dict[str, str] = {}
        fetched = 0
        for tour_url, tour_rows in ordered:
            badge_sold: dict[str, bool] = {}
            for r in tour_rows:
                if r.start_date:
                    badge_sold[r.start_date] = (
                        badge_sold.get(r.start_date, False) or r.is_sold_out)
            artist_hint = tour_rows[0].title_ja

            if fetched < self.tour_fetch_cap:
                try:
                    if tour_url not in cache:
                        cache[tour_url] = self.fetch(tour_url)
                        fetched += 1
                    page = parse_tour(cache[tour_url], tour_url=tour_url)
                except RuntimeError:
                    yield from self._deferred(tour_rows)   # fetch failed
                    continue
                yield from self._legs_to_events(
                    page, tour_url, badge_sold, artist_hint, floor_date)
            else:
                yield from self._deferred(tour_rows)

    def _legs_to_events(self, page: dict, tour_url: str,
                        badge_sold: dict[str, bool],
                        artist_hint: str | None,
                        floor_date: str | None = None) -> Iterator[Event]:
        # Resolve venues first: drop legs that don't map to a curated Kanto
        # venue, remembering Kanto-prefecture misses for the report. Past-
        # month legs (before the listing window) are dropped silently.
        kept: list[tuple[dict, str]] = []
        for leg in page["legs"]:
            if floor_date and leg["date"] < floor_date:
                continue
            key = resolve_venue(leg["venue"])
            if key is None:
                if leg["pref"] in KANTO_PREF_KANJI:
                    self.skipped_venues.add(leg["venue"])
                continue
            kept.append((leg, key))

        # Two Kanto legs on the same date need distinct source_urls; the
        # common one-leg-per-date case keeps the bare "#<date>" so a
        # cap-deferred row from an earlier run resolves to the same event.
        date_counts = Counter(leg["date"] for leg, _ in kept)
        artist = page["artist"] or artist_hint
        title = page["title"] or artist or artist_hint

        for leg, key in kept:
            date = leg["date"]
            frag = f"#{date}-{key}" if date_counts[date] > 1 else f"#{date}"
            lineup: list[str] = []
            if artist:
                lineup.append(artist)
            for g in leg["guests"]:
                if g and g not in lineup:
                    lineup.append(g)
            nonmusic = tu.is_nonmusic(title or "") or tu.is_nonmusic(artist or "")
            yield Event(
                source=self.source_id,
                source_url=tour_url + frag,
                title_ja=title,
                category=Category.OTHER if nonmusic else Category.MUSIC,
                start_date=date,
                open_time=leg["open_time"], start_time=leg["start_time"],
                venue_name=leg["venue"],          # RAW; canonicalized at export
                price_text=leg["price_text"], price_min=leg["price_min"],
                is_free=leg["is_free"],
                is_sold_out=leg["sold_out"] or badge_sold.get(date, False),
                ticket_links=leg["ticket_links"],
                lineup=lineup,
            )

    @staticmethod
    def _deferred(tour_rows: list[Event]) -> Iterator[Event]:
        """Yield cap-deferred calendar rows as minimal Events (date + artist
        + sold-out badge, no venue). A later run enriches them once the tour
        page fits the fetch budget."""
        seen: set[str] = set()
        for r in tour_rows:
            if not r.start_date:
                continue
            r.source_url = f"{r.source_url}#{r.start_date}"
            if r.source_url in seen:
                continue
            seen.add(r.source_url)
            yield r

    # -------------------------------------------------------- listing parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure calendar parse -> minimal Events, ONE per Kanto calendar row.

        Each carries only what scrape() needs to group and enrich: the tour
        URL (source_url), the date, the artist (title_ja) and the listing
        sold-out badge. venue_name stays None until a leg is parsed. Non-Kanto
        rows (osaka/aichi/…) are dropped here. Dates need the `month` context
        (the cell shows only a day number); without it, no rows are emitted.
        """
        soup = BeautifulSoup(html, "lxml")
        events: list[Event] = []
        for blk in soup.select(".p-calendar__live"):
            a = blk.find("a", href=True)
            if not a:
                continue
            area = blk.find(class_="p-calendar__areaLabel")
            pref_class = None
            if area:
                for c in area.get("class", []):
                    mm = AREA_RE.match(c)
                    if mm:
                        pref_class = mm.group(1)
                        break
            if pref_class not in KANTO_CLASSES:
                continue

            date = self._cell_date(blk, month)
            if not date:
                continue

            area_text = area.get_text(strip=True) if area else ""
            artist = a.get_text(" ", strip=True)
            if area_text and artist.startswith(area_text):
                artist = artist[len(area_text):]
            artist = _clean(artist)
            if not artist:
                continue

            badge_divs = blk.find_all("div", recursive=False)
            badge = badge_divs[0].get_text(" ", strip=True) if badge_divs else ""

            events.append(Event(
                source=self.source_id,
                source_url=urljoin(EVENT_BASE, a["href"]),
                title_ja=artist,
                category=(Category.OTHER if tu.is_nonmusic(artist)
                          else Category.MUSIC),
                start_date=date,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(badge)),
                venue_name=None,
            ))
        return events

    @staticmethod
    def _cell_date(blk, month: dt.date | None) -> str | None:
        if month is None:
            return None
        cell = blk.find_parent("td")
        label = cell.find(class_="p-calendar__label") if cell else None
        if label is None:
            return None
        txt = label.get_text(strip=True)
        if not txt.isdigit():
            return None
        try:
            return dt.date(month.year, month.month, int(txt)).isoformat()
        except ValueError:
            return None
