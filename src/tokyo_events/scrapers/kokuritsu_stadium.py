"""Scraper for MUFG Stadium — former 国立競技場 / Japan National Stadium —
https://jns-e.com

Operated by "Japan National Stadium Entertainment Inc." Renamed
"MUFGスタジアム" under naming rights (Jan 2026); the old "国立競技場" name is
kept only as a secondary label site-wide, so the display name is
"MUFGスタジアム（国立競技場）".

This is a STADIUM: the event calendar is overwhelmingly sport (J-League,
rugby, friendlies) with only a handful of concerts a year. Every listing row
carries the site's OWN category tag — a paragraph whose text is 音楽 (music)
or スポーツ (sport). We trust that tag: 音楽 -> Category.MUSIC, anything else
-> Category.OTHER. Non-concert rows are kept, not silently dropped (the export
layer can filter Category.OTHER for this source); this icon tag is THE
single most important field for this source.

2026-09 site move: the old ``/event/`` listing died (every month page, and
even ``/event/archive/``, now render only "この月のイベント予定はありません" —
confirmed live 2026-09-24) in favor of a new ``/calendar/`` listing. Same
per-event slugs, just relocated: the Mrs. GREEN APPLE show that used to sit
at ``/event/20260309-453/`` is now ``/calendar/20260309-453/``. This parser
targets ``/calendar/`` instead; everything else about the page shape carried
over unchanged (still fully server-rendered Next.js App Router):

Fully server-rendered: the DOM holds every event in
``<ul class="p-schedule-calendar-list">`` as
``<li><a href="/calendar/{YYYYMMDD}...">``. The same data is duplicated
inside an RSC flight payload in ``<script>`` tags, but ``<script>`` content
is raw text to the HTML parser, so anchor-walking the DOM sees each event
exactly once.

Month pages: ``/calendar/page/{YYYYMM}/`` ; bare ``/calendar/`` = current
month. Unlike the dead ``/event/`` pager, ``/calendar/`` still 404s cleanly
once you walk past its available window (its own month tabs span roughly a
year either side of today), so the forward month-walk keeps working off a
real stop condition instead of silently paging through empty months forever.
The listing gives every date fully qualified (year + MM/DD in their own
spans, repeated per day for a multi-day run), so ``parse()`` needs no year
inference and is deterministic.

One listing field was dropped in the redesign: ``アーティスト`` (lineup) no
longer appears on the calendar rows, only on each event's own
``/calendar/{slug}/`` detail page — so ``supports_detail`` is now True
(previously False, because the old listing already had everything). Detail
pages otherwise still mirror the listing (dates, 開場/開演 times) plus a link
to the promoter's own 特設サイト and a ticket-inquiry contact — still NO ¥
price and NO standard playguide links (re-verified 2026-09-24) — so
``parse_detail`` only adds the recovered lineup on top of the harmless
generic base enrichment (which finds nothing to fill there).

source_url stays the real per-event page (``https://jns-e.com/calendar/
{slug}/``); the slug itself is untouched by the move, only the path prefix
changed from ``/event/`` to ``/calendar/`` — so every stored row's identity
changes too (see ``announce.BACKFILL["kokuritsu_stadium"]``, which keeps
this migration from flooding the 新着 feed with re-keyed old shows).

CAUTION: the 8-digit date in a detail slug (``/calendar/20260309-453/``) is
the article's publish date, NOT the event date — always read the date from
the on-page 日程 field (this parser does).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper, NotFoundError
from . import textutils as tu

VENUE = dict(
    venue_name="MUFGスタジアム（国立競技場）",
    venue_area="Sendagaya",
    address="10-1 Kasumigaokamachi, Shinjuku-ku, Tokyo",
    lat=35.6779, lng=139.7147,
)

# Detail links only: "/calendar/" + 8-digit date prefix. This deliberately
# EXCLUDES the pager ("/calendar/page/YYYYMM/"), archive ("/calendar/archive/")
# and the bare current-month link ("/calendar/"), so a structural change to
# the list markup shows up as found=0 rather than as garbage nav rows.
EVENT_HREF_RE = re.compile(r"^/calendar/\d{8}")
# Schedule spans render as "2026 07/04 土 [2026 07/05 日]" — key off the date
# shape, not the span classes.
SCHED_DATE_RE = re.compile(r"(\d{4})\s*(\d{1,2})\s*/\s*(\d{1,2})")
# Concert start-time field: "開場16:30 開演18:30" (kaijou = OPEN, kaien = START).
# Sports use "17:40 キックオフ" instead, which carries neither marker.
KAIJO_RE = re.compile(r"開場\s*(\d{1,2}:\d{2})")
KAIEN_RE = re.compile(r"開演\s*(\d{1,2}:\d{2})")


class KokuritsuStadiumScraper(BaseScraper):
    source_id = "kokuritsu_stadium"
    source_name = "MUFG Stadium (Kokuritsu)"
    BASE = "https://jns-e.com"
    supports_detail = True         # listing lost アーティスト in the 09/26 move

    def __init__(self, months_ahead: int = tu.HORIZON_MONTHS, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = tu.jst_today().replace(day=1)
        seen: set[str] = set()
        # current month lives at the bare /calendar/ ...
        yield from self._emit(self.fetch(f"{self.BASE}/calendar/"), seen)
        # ... then walk forward month pages. The pager exposes ~13 months; a
        # month that far out simply won't fetch, so stop on the first failure.
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/calendar/page/{m.year}{m.month:02d}/"
            try:
                html = self.fetch(url)
            except NotFoundError:
                break
            yield from self._emit(html, seen)

    def _emit(self, html: str, seen: set[str]) -> Iterable[Event]:
        for ev in self.parse(html):
            if ev.source_url in seen:
                continue          # same event listed in two months
            seen.add(ev.source_url)
            yield ev

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not EVENT_HREF_RE.match(a["href"]):
                continue
            url = urljoin(self.BASE, a["href"])
            block = a.find_parent("li") or a
            ev = self._parse_block(block, url)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, block, url: str) -> Event | None:
        # Map each field by its Japanese <dt> label (robust to class churn):
        # 日程 (dates), 開始時間 (times), 主催者 (organizer, unused).
        dds: dict[str, object] = {}
        for dt_tag in block.find_all("dt"):
            dd = dt_tag.find_next_sibling("dd")
            if dd is not None:
                dds[dt_tag.get_text(strip=True)] = dd

        sched = dds.get("日程")
        if sched is None:
            return None
        dates: list[str] = []
        for y, mo, d in SCHED_DATE_RE.findall(sched.get_text(" ", strip=True)):
            try:
                dates.append(dt.date(int(y), int(mo), int(d)).isoformat())
            except ValueError:
                continue
        if not dates:
            return None
        start_date = dates[0]
        end_date = dates[-1] if len(dates) > 1 else None

        title_p = block.find("p", class_="p-schedule-calendar-list__title")
        title = (re.sub(r"\s+", " ", title_p.get_text(" ", strip=True)).strip()
                 if title_p else None)
        if not title:
            return None

        # Category: trust the site's own tag (label text 音楽 / icon filename).
        # Backstop: a clearly non-music title still lands in OTHER even if the
        # tag says music (belt and suspenders — we never invent keyword lists).
        cat = block.find("div", class_="p-schedule-calendar-list__category")
        cat_text = cat.get_text(" ", strip=True) if cat else ""
        icon_src = ""
        if cat is not None:
            img = cat.find("img")
            if img is not None:
                icon_src = img.get("src", "")
        is_music = ("音楽" in cat_text) or ("music" in icon_src)
        if is_music and tu.is_nonmusic(title):
            is_music = False
        category = Category.MUSIC if is_music else Category.OTHER

        open_time = start_time = None
        time_dd = dds.get("開始時間")
        if time_dd is not None:
            ttext = time_dd.get_text(" ", strip=True)
            mo_ = KAIJO_RE.search(ttext)
            ms_ = KAIEN_RE.search(ttext)
            open_time = mo_.group(1) if mo_ else None
            start_time = ms_.group(1) if ms_ else None

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category,
            start_date=start_date, end_date=end_date,
            open_time=open_time, start_time=start_time,
            **VENUE,
        )

    def parse_detail(self, html: str, ev: Event) -> Event:
        """Generic enrichment first (a harmless no-op here: detail pages
        carry no ¥ price or playguide links, re-verified 2026-09-24); then
        recover アーティスト (lineup), the one field the 2026-09 redesign
        dropped from the calendar listing rows."""
        ev = super().parse_detail(html, ev)
        if not ev.lineup:
            soup = BeautifulSoup(html, "lxml")
            for dt_tag in soup.find_all("dt"):
                if dt_tag.get_text(strip=True) != "アーティスト":
                    continue
                dd = dt_tag.find_next_sibling("dd")
                if dd is None:
                    break
                artist = re.sub(r"\s+", " ", dd.get_text(" ", strip=True)).strip()
                if artist:
                    ev.lineup = [artist]
                break
        return ev
