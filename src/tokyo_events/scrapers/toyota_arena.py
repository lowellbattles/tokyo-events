"""Scraper for TOYOTA ARENA TOKYO — https://www.toyota-arena-tokyo.jp

A ~10,000-seat multipurpose arena in Aomi / Odaiba, Koto-ku (opened Oct 2025;
home of B.LEAGUE's ALVARK TOKYO). The /events/ listing is a Next.js App
Router (RSC) page: every event card lives inside the
`self.__next_f.push([1,"..."])` flight-payload script strings rather than
semantic DOM, but a plain GET returns all of it — no JS execution needed.

We split the served HTML into per-card chunks on each event `li` node
(`["$","li","{slug}",{...}]`) and, within each chunk, key off TEXT
conventions rather than Next.js class hashes (which churn on redeploy):

  * DATE   — a badge string of the shape ``YYYY.M.D(曜)`` e.g. ``2026.7.11(土)``
  * TITLE  — the KV image ``alt`` (always inline; duplicates the title),
             with the bold title ``<p>`` as a fallback
  * ARTIST — an ``アーティスト：`` label -> lineup (slash-separated)
  * TIMES  — OPEN/START written 開場 / 開演 / 開始 (either marker-first or
             time-first, single- or multi-part shows)
  * LINK   — the card's ``href`` (internal /events/{slug} or /pages/{slug},
             or an external promoter site)

If the flight shape changes, no card yields a date badge and parse() returns
[] (loud structural failure), per the house rules.

Month pages: ``/events/?year=YYYY&month=MM`` (MM zero-padded); bare
``/events/`` is the current month. We walk forward month by month and stop on
the first empty future month (zepp precedent). Each card is ONE calendar date
— multi-day runs render as separate cards with their own slug + image — so we
keep one Event per date and append a ``#YYYY-MM-DD`` fragment to source_url to
keep dedupe keys unique (yokohama_arena precedent).

Mixed calendar: concerts / idol / K-pop fanmeetings / artist-led events are
MUSIC; combat sports (RIZIN) and the resident ALVARK TOKYO / B.LEAGUE
basketball games are tagged Category.OTHER (kept, not skipped) via
``tu.is_nonmusic`` plus the resident-team label. There are no ticket prices on
the listing and detail pages mostly link out, so ``supports_detail`` is False
— the listing already carries the facts.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(
    venue_name="TOYOTA ARENA TOKYO（トヨタアリーナ東京）",
    venue_area="Odaiba",
    address="Aomi, Koto-ku, Tokyo",
    # lat/lng deliberately omitted: the probe's ~35.619,139.781 was flagged
    # approximate/unconfirmed, and the house rule is "lat/lng only if confident".
)

# --- flight-payload extractors (operate on the doubly-escaped HTML text) ---
# Each event card: ["$","li","{slug}",{"className":...}]  ->  \"li\",\"slug\",{
CARD_RE = re.compile(r'\\"li\\",\\"([A-Za-z0-9_-]+)\\",\{')
# Date badge string, e.g. \"children\":\"2026.7.11(土)\"
DATE_RE = re.compile(
    r'\\"children\\":\\"(20\d{2})\.(\d{1,2})\.(\d{1,2})\((?:日|月|火|水|木|金|土)\)\\"')
HREF_RE = re.compile(r'\\"href\\":\\"([^\\"]+)\\"')
ALT_RE = re.compile(r'\\"alt\\":\\"([^\\"]*)\\"')
# Fallback title: the bold <p> whose children are [\"TITLE\",...]
TITLE_P_RE = re.compile(r'font-bold[^{}]{0,160}?\\"children\\":\[\\"([^\\"]+)\\"')
# Artist label span is immediately followed by the value span:
#   アーティスト：\",\"KARA\"   /   ...\",\" 黒夢\"
ARTIST_RE = re.compile(r'アーティスト：\\",\\"([^\\"]*)\\"')
# First span text that carries an OPEN/START marker holds the show times.
TIMES_RE = re.compile(r'\\"children\\":\\"([^\\"]*(?:開場|開演|開始)[^\\"]*)\\"')
# Room badge (MAIN ARENA, ...) — a bordered span; nice-to-have tag only.
ROOM_RE = re.compile(r'border border-black[^{}]{0,160}?\\"children\\":\\"([^\\"]+)\\"')

_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_OPEN_MK = re.compile("開場")
_START_MK = re.compile("開演|開始")
# Resident B.LEAGUE team. tu.is_nonmusic already covers Bリーグ/B.LEAGUE/RIZIN;
# this venue-specific label catches ALVARK games titled without those tokens.
_ALVARK_RE = re.compile(r"アルバルク|ALVARK", re.I)


def _first(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def _clean(s: str | None) -> str | None:
    """Collapse whitespace (incl. NBSP / full-width space, both Unicode
    whitespace) and strip; return None if nothing is left."""
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _nearest(times: list[tuple[int, int, str]], mk: re.Match | None):
    """Pick the time span closest (by character gap) to a marker match."""
    if not mk or not times:
        return None
    ms, me = mk.start(), mk.end()

    def dist(t):
        ts, te, _ = t
        if te <= ms:
            return ms - te            # time sits before the marker
        if ts >= me:
            return ts - me            # time sits after the marker
        return 0

    return min(times, key=dist)


def _parse_jp_times(text: str) -> tuple[str | None, str | None]:
    """OPEN/START from a 開場/開演/開始 block. Handles marker-first
    ("開場17:30") and time-first ("16:00開場") orders and multi-part shows
    (returns the first part's times). START is chosen from the times NOT
    already claimed by 開場, so "開場 17:00／開演 18:30" doesn't hand 17:00 to
    both markers."""
    times = [(m.start(), m.end(), m.group()) for m in _TIME_RE.finditer(text)]
    if not times:
        return None, None
    o = _nearest(times, _OPEN_MK.search(text))
    remaining = [t for t in times if t is not o]
    s = _nearest(remaining, _START_MK.search(text))
    return (o[2] if o else None, s[2] if s else None)


class ToyotaArenaScraper(BaseScraper):
    source_id = "toyota_arena_tokyo"
    source_name = "TOYOTA ARENA TOKYO"
    BASE = "https://www.toyota-arena-tokyo.jp"
    supports_detail = False        # the listing already carries the facts

    def __init__(self, months_ahead: int = 6, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        # bare /events/ defaults to the current calendar month
        yield from self.parse(self.fetch(f"{self.BASE}/events/"), today=first)
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/events/?year={m.year}&month={m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break               # months that far out may not exist yet
            # A sporadically-booked arena has normal interior empty months —
            # card dates are absolute (YYYY.M.D), so walking the full window
            # is safe and an empty month is just one cheap fetch.
            yield from self.parse(html, today=m)

    def parse(self, html: str, today: dt.date | None = None,
              month: dt.date | None = None, **context) -> list[Event]:
        # Dates in the flight payload are absolute (YYYY.M.D), so today/month
        # are accepted for signature parity but not needed for correctness.
        events: dict[str, Event] = {}
        starts = [(m.start(), m.group(1)) for m in CARD_RE.finditer(html)]
        for idx, (pos, _slug) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(html)
            chunk = html[pos:end]

            dm = DATE_RE.search(chunk)
            if not dm:
                continue            # nav <li> or a card whose date didn't render
            try:
                date = dt.date(int(dm.group(1)), int(dm.group(2)),
                               int(dm.group(3))).isoformat()
            except ValueError:
                continue

            # Title: KV image alt is inline for every card and duplicates the
            # title; the bold <p> is a fallback if a card lacks an image.
            title = _clean(_first(ALT_RE, chunk)) or _clean(_first(TITLE_P_RE, chunk))
            if not title:
                continue

            href = _first(HREF_RE, chunk)
            if not href:
                continue
            url = href if href.startswith("http") else urljoin(self.BASE + "/", href)
            url = f"{url}#{date}"    # keep multi-date / shared-link cards distinct

            lineup: list[str] = []
            artist = _clean(_first(ARTIST_RE, chunk))
            if artist:
                lineup = [a.strip() for a in re.split(r"[/／]", artist) if a.strip()]

            times_text = _first(TIMES_RE, chunk)
            open_time, start_time = (
                _parse_jp_times(times_text) if times_text else (None, None))

            tags: list[str] = []
            room = _clean(_first(ROOM_RE, chunk))
            if room:
                tags.append(room)

            blob = f"{title} {' '.join(lineup)}"
            category = (Category.OTHER
                        if tu.is_nonmusic(blob) or _ALVARK_RE.search(blob)
                        else Category.MUSIC)

            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, category=category, start_date=date,
                open_time=open_time, start_time=start_time,
                lineup=lineup, tags=tags,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(blob)),
                **VENUE,
            )
            if ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())
