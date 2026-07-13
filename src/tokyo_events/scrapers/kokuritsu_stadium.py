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

Fully server-rendered Next.js (App Router): the DOM holds every event in
``<ul class="p-event-list">`` as ``<li><a href="/event/{YYYYMMDD}...">``. The
same data is duplicated inside an RSC flight payload in ``<script>`` tags, but
``<script>`` content is raw text to the HTML parser, so anchor-walking the DOM
sees each event exactly once.

Month pages: ``/event/page/{YYYYMM}/`` ; bare ``/event/`` = current month. The
listing gives every date fully qualified (year + MM/DD in their own spans), so
``parse()`` needs no year inference and is deterministic. Detail pages only
mirror the listing fields (dates, 開場/開演 times, artist) plus a link to the
promoter's own 特設サイト and a ticket-inquiry contact — they carry NO ¥ price
and NO standard playguide links (verified live 2026-07-13), so the listing is
already complete and ``supports_detail`` is False (no per-event fetch needed).

CAUTION: the 8-digit date in a detail slug (``/event/20260309-453/``) is the
article's publish date, NOT the event date — always read the date from the
on-page 日程 field (this parser does).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(
    venue_name="MUFGスタジアム（国立競技場）",
    venue_area="Sendagaya",
    address="10-1 Kasumigaokamachi, Shinjuku-ku, Tokyo",
    lat=35.6779, lng=139.7147,
)

# Detail links only: "/event/" + 8-digit date prefix. This deliberately
# EXCLUDES the pager ("/event/page/YYYYMM/"), archive ("/event/archive/") and
# the bare current-month link ("/event/"), so a structural change to the list
# markup shows up as found=0 rather than as garbage nav rows.
EVENT_HREF_RE = re.compile(r"^/event/\d{8}")
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
    supports_detail = False        # listing already carries every wanted field

    def __init__(self, months_ahead: int = 4, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        # current month lives at the bare /event/ ...
        yield from self._emit(self.fetch(f"{self.BASE}/event/"), seen)
        # ... then walk forward month pages. The pager exposes ~13 months; a
        # month that far out simply won't fetch, so stop on the first failure.
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/event/page/{m.year}{m.month:02d}/"
            try:
                html = self.fetch(url)
            except RuntimeError:
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
        # 日程 (dates), 開始時間 (times), アーティスト (artist), 主催者 (organizer).
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

        head = block.find("p", class_="p-event-list__head")
        title = (re.sub(r"\s+", " ", head.get_text(" ", strip=True)).strip()
                 if head else None)
        if not title:
            return None

        # Category: trust the site's own icon tag (text 音楽 / class "music").
        # Backstop: a clearly non-music title still lands in OTHER even if the
        # icon says music (belt and suspenders — we never invent keyword lists).
        icon = block.find("div", class_="p-event-list__icon")
        icon_text = icon.get_text(" ", strip=True) if icon else ""
        icon_cls = ""
        if icon is not None:
            p = icon.find("p")
            if p is not None and p.get("class"):
                icon_cls = " ".join(p["class"])
        is_music = ("音楽" in icon_text) or ("music" in icon_cls)
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

        lineup: list[str] = []
        art_dd = dds.get("アーティスト")
        if art_dd is not None:
            artist = re.sub(r"\s+", " ", art_dd.get_text(" ", strip=True)).strip()
            if artist:
                lineup = [artist]

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category,
            start_date=start_date, end_date=end_date,
            open_time=open_time, start_time=start_time,
            lineup=lineup, **VENUE,
        )
