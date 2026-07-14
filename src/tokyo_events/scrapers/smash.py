"""Scraper for SMASH — https://smash-jpn.com

SMASH is a concert PROMOTER, not a venue: it books international/indie
acts across many Tokyo-area halls (Zepp group, LIQUIDROOM, EX THEATER
ROPPONGI, duo MUSIC EXCHANGE, ...) plus a long tail of halls this project
hasn't curated yet (KANDA SQUARE HALL, Yogibo META VALLEY, 昭和女子大学
人見記念講堂, ...) and, on the nationwide calendar, cities entirely outside
our Tokyo/Kanagawa/Chiba/Saitama scope ("盛岡 CLUB CHANGE WAVE", "青森
Quarter"). ``venues.resolve_venue`` is the geography/curation gate, same
role it plays for sogo_tokyo/creativeman — unresolvable venues are dropped
and their raw strings collected for the operator to extend the registry
with, never guessed at with local prefecture logic. ``promoters.py``
already lists "smash_jpn" in PROMOTER_SOURCES, so duplicate bookings at
venues we also scrape directly (same date + venue + artist) fold into the
venue's own record at export time — no extra handling needed here.

Listing: /calendar/?year=YYYY&month=M&p=3 — a static, server-rendered
month calendar. ``month`` is UNPADDED (M, not MM). ``p`` is the site's own
region filter (1=Hokkaido/Tohoku ... 3=首都圏/Kanto ... 7=Kyushu); p=3
narrows the fetch to Kanto-relevant shows AT THE SOURCE, so it is always
passed explicitly (there is no "current month defaults to Kanto" bare
URL — unlike sogo_tokyo's calendar, every month here is fetched with the
same explicit ``year=&month=&p=3`` query). Structure is one ``<table>``
of week rows; each day ``<td>`` holds a ``<p class="day">`` (weekday +
day-of-month, no ISO date — composed from the page's month/year context)
and a ``<ul>`` of zero-or-more show ``<li>``:

    <li><a href="/live/?id=4712">MITSKI</a><span>MITSKI<br>
    会場:Zepp DiverCity (TOKYO)<br>開場:18:00&nbsp;開演:19:00<br>
    <img src=".../ico_ippan.png" ...> 前売りアリ</span></li>

The title appears twice — the ``<a>`` link text (occasionally a
shortened/combined tour name) and the ``<span>``'s first line (fuller
text, sometimes itself a multi-artist tour title, e.g. "Age Factory x
ENTH x Paledusk presents「GOBLIN」TOUR 2026"). We key off the fuller
``<span>`` line, falling back to the ``<a>`` text only if that line is
empty. The calendar is artist-first — most rows are simply the artist's
name — so a title with no tour/multi-act markers (TOUR, PRESENTS, ×, " x
", VS, &, meets, "produced by") also becomes the event's ``lineup``; a
title carrying one of those markers is left as a bare title with no
lineup guess. 開場/開演 (OPEN/START) times sit on their own ``<span>``
line, plain-Japanese-labelled — ``tu.parse_times()`` only recognizes the
Latin OPEN/START words, so a small local regex reads these instead.
Status is read straight out of the cell text: 前売りアリ / 当日券アリ are
on-sale (not sold out); the "SOLD OUT" text next to ``ico_soldout.png``
IS matched by ``tu.SOLD_OUT_RE`` (it's plain text, not just an icon
``alt``); a cancelled show drops the ticket-status line entirely and
prints ``《公演中止》`` instead — kept as a normal Event (facts, not
dropped — the bay_hall precedent) with ``tags=["cancelled"]`` and the
title left untouched.

Detail page (/live/?id=N) repeats one ``<section>`` per tour leg/city
under "Live Schedule" (``div.sche``) -- multi-city tours (e.g. STEREOLAB
Osaka/Nagoya/Tokyo×2 legs, all sharing one detail id) each have their own
address/times/price/ticket-links/sold-out block, so ``parse_detail``
picks the ``<section>`` whose own header matches this event's date/venue
(``_match_leg``) before reading anything, rather than blending every
city's numbers together. Within a leg, OPEN/START are spelled out in
English ("OPEN 18:00 START 19:00", already readable by
``tu.parse_times``) and price tiers are ¥-prefixed ("前売り:￥13,000",
already readable by the generic ¥-keyed parser) but sit next to a
"ドリンク代別" (drink charge separate) note, so ``tu.strip_drink_charges``
still runs first per project convention. Ticket-info tabs list playguide
anchors (eplus/pia/lawson) picked up by ``tu.extract_ticket_links``.

Parsers key off the ``/live/?id=`` URL shape and the site's own JP field
labels (会場/開場/開演), not CSS class names, so a template change fails
loud (found=0) rather than silently parsing nothing.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper

# Detail permalink: /live/?id=<numeric id> — the structural key for both
# the listing scan and the fixture URL shape.
DETAIL_HREF_RE = re.compile(r"/live/\?id=\d+")

# Trailing day-of-month digits in a day cell's <p class="day"> (weekday
# abbreviation + <br> + bare day number, e.g. "TUE" + "28" -> "TUE28").
DAY_RE = re.compile(r"(\d{1,2})\s*$")

# The venue line inside a listing <span>: "会場:Zepp DiverCity (TOKYO)".
VENUE_LINE_RE = re.compile(r"^会場[:：]\s*(.+)$")

# Listing times are JP-labelled (開場/開演), not the Latin OPEN/START words
# tu.parse_times() looks for, e.g. "開場:18:00\xa0開演:19:00".
JP_TIME_PAIR_RE = re.compile(
    r"開場[:：]?\s*(\d{1,2}:\d{2}).*?開演[:：]?\s*(\d{1,2}:\d{2})")

CANCELLED_RE = re.compile(r"公演中止")

# Best-effort "this title is a tour/multi-act billing, not a bare artist
# name" signal -- excludes it from the guessed lineup. Precision-first
# (project convention, see textutils.NONMUSIC_RE): letting an odd
# multi-act title into lineup as a single (wrong) "artist" is worse than
# occasionally leaving a genuine solo artist's lineup empty.
MULTI_ACT_RE = re.compile(
    r"TOUR|PRESENTS|FES(?:TIVAL)?|\sVS\.?\s|×|\sx\s|&|meets|produced\s+by",
    re.I)


class SmashScraper(BaseScraper):
    source_id = "smash_jpn"
    source_name = "SMASH"
    BASE = "https://smash-jpn.com"
    supports_detail = True

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        #: raw venue strings resolve_venue() couldn't place — distinct,
        #: accumulated across scrape()/parse() calls for operator visibility
        #: (extend venues.CANONICAL/_EXTRA_ALIASES to pick these up).
        self.skipped_venues: set[str] = set()

    # ---------------------------------------------------------------- fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/calendar/?year={m.year}&month={m.month}&p=3"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break            # far-future months eventually error out
            for ev in self.parse(html, month=m):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse: HTML string -> list[Event]. ``month`` pins the
        page's year/month (day cells carry neither) — scrape() always passes
        it; without it a date can't be resolved, so rows are dropped."""
        if not html or month is None:
            return []
        soup = BeautifulSoup(html, "lxml")
        # Scope to the calendar table only: the page also links every
        # currently-touring artist's detail page from a sidebar carousel
        # (#artistLine), which matches the same /live/?id= URL shape but
        # isn't a calendar row.
        table = soup.find("table")
        if table is None:
            return []

        rows: list[tuple[str, str, Event]] = []
        for a in table.find_all("a", href=DETAIL_HREF_RE):
            parsed = self._parse_row(a, month)
            if parsed:
                rows.append(parsed)

        # A detail id can repeat across dates (multi-city tour legs share
        # one page) -- only fragment the URL when a given id actually spans
        # >1 distinct date (tachikawa_stage_garden / sogo_tokyo precedent),
        # so the common single-date show keeps a clean bare detail URL.
        dates_by_url: dict[str, set[str]] = defaultdict(set)
        for url, date, _ in rows:
            dates_by_url[url].add(date)

        events: dict[str, Event] = {}
        for url, date, ev in rows:
            surl = f"{url}#{date}" if len(dates_by_url[url]) > 1 else url
            ev.source_url = surl
            if surl not in events:
                events[surl] = ev
        return list(events.values())

    def _parse_row(self, a: Tag, month: dt.date) -> tuple[str, str, Event] | None:
        url = urljoin(self.BASE, a["href"])

        td = a.find_parent("td")
        day_p = td.find("p", class_="day") if td else None
        if day_p is None:
            return None
        day_m = DAY_RE.search(day_p.get_text(strip=True))
        if not day_m:
            return None
        try:
            date = dt.date(month.year, month.month, int(day_m.group(1))).isoformat()
        except ValueError:
            return None

        span = a.find_next_sibling("span")
        if span is None:
            return None
        lines = _lines(span)
        if not lines:
            return None

        a_text = _clean(a.get_text(" ", strip=True))
        title = lines[0] or a_text
        if not title:
            return None

        venue_raw = None
        for line in lines[1:]:
            vm = VENUE_LINE_RE.match(line)
            if vm:
                venue_raw = _clean(vm.group(1))
                break
        if not venue_raw:
            return None

        # Geography/curation gate: p=3 pre-filters to Kanto at the source,
        # but city-prefixed strings ("盛岡 CLUB CHANGE WAVE") and halls we
        # haven't curated yet still leak through -- drop, don't guess.
        if resolve_venue(venue_raw) is None:
            self.skipped_venues.add(venue_raw)
            return None

        full_text = " ".join(lines)
        cancelled = bool(CANCELLED_RE.search(full_text))
        sold_out = bool(tu.SOLD_OUT_RE.search(full_text))

        open_time = start_time = None
        tm = JP_TIME_PAIR_RE.search(full_text)
        if tm:
            open_time, start_time = tm.group(1), tm.group(2)

        category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC
        # Artist-first calendar: a title with no tour/multi-act markers IS
        # the artist -- feed it into lineup too (mirrors creativeman's
        # artist-as-title convention), letting promoters.py's export-time
        # merge match it against the venue's own scraped record.
        lineup = [title] if title and not MULTI_ACT_RE.search(title) else []

        ev = Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time,
            venue_name=venue_raw, venue_area=None, address=None,
            lat=None, lng=None,
            lineup=lineup, is_sold_out=sold_out,
            tags=["cancelled"] if cancelled else [],
        )
        return (url, date, ev)

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Detail-page OPEN/START are spelled out in English ("OPEN 18:00
        START 19:00"), unlike the 開場/開演 listing calendar, so the generic
        tu.parse_times() applies directly here. Price tiers are already
        ¥-prefixed too, but sit next to a "ドリンク代別" (drink charge
        separate) note, so strip_drink_charges still runs first.

        Multi-city tour pages repeat ALL of this (times/prices/ticket
        links/sold-out) once per city under "Live Schedule" (div.sche >
        one <section> per leg) -- reading the whole block would silently
        blend another city's numbers into this event, so ``_match_leg``
        picks the <section> whose own header matches this event's date
        and/or venue first, falling back to the page as a whole only when
        nothing matches (e.g. a structure change) so behavior degrades to
        the pre-multi-leg-aware default rather than finding nothing.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        leg = self._match_leg(soup, ev)
        zone_text = leg.get_text(" ", strip=True) if leg is not None else text

        if not (ev.open_time or ev.start_time):
            ev.open_time, ev.start_time = tu.parse_times(zone_text)

        if ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(
                tu.strip_drink_charges(zone_text))

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(leg or soup, zone_text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(zone_text):
            ev.is_sold_out = True
        return ev

    @staticmethod
    def _match_leg(soup: BeautifulSoup, ev: Event) -> Tag | None:
        """Return the div.sche <section> (tour leg) matching ``ev``'s own
        date/venue, or the first section if none match, or None if the
        page has no "Live Schedule" block at all (caller then falls back
        to the whole page)."""
        sche = soup.find("div", class_="sche")
        if sche is None:
            return None
        sections = sche.find_all("section")
        if not sections:
            return sche

        date_variants: list[str] = []
        if ev.start_date:
            try:
                y, mo, d = ev.start_date.split("-")
                date_variants = [f"{y}/{mo}/{d}", f"{y}/{int(mo)}/{int(d)}"]
            except ValueError:
                pass
        venue_norm = _clean(ev.venue_name or "")

        for section in sections:
            head = section.get_text(" ", strip=True)
            if (any(dv in head for dv in date_variants)
                    or (venue_norm and venue_norm in head)):
                return section
        return sections[0]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _lines(span: Tag) -> list[str]:
    """Split a listing cell's <span> into its <br>-delimited lines (title /
    venue / times / status) -- <br> is the only structural line boundary
    the site gives us, no CSS hooks on the individual lines."""
    lines: list[str] = []
    buf: list[str] = []
    for node in span.contents:
        if isinstance(node, Tag) and node.name == "br":
            lines.append(_clean("".join(buf)))
            buf = []
        elif isinstance(node, NavigableString):
            buf.append(str(node))
        else:                      # <img alt=""> etc. carry no useful text
            buf.append(node.get_text())
    lines.append(_clean("".join(buf)))
    return [ln for ln in lines if ln]
