"""Scraper for Kanadevia Hall — https://www.tokyo-dome.co.jp/tdc-hall/

Kanadevia Hall is the renamed (Apr 2025 naming rights) former TOKYO DOME
CITY HALL / 東京ドームシティホール, a ~3,000-cap multipurpose hall inside
Tokyo Dome City, operated by Tokyo Dome Corp. The official schedule lives
at /tdc-hall/event/ as a single static server-side page.

Structure (keys off text conventions + stable c-mod-calender__* classes):
  <div class="c-mod-tab__body">            one per month tab
    <p class="c-ttl-set-calender">2026年07月</p>
    <table class="c-mod-calender">
      <tr class="c-mod-calender__item">     one per calendar DAY
        <th><span class="c-mod-calender__day">01</span>
            <span class="c-mod-calender__day">(水)</span></th>
        <td class="c-mod-calender__detail">
          <span class="c-txt-tag__item">コンサート</span>   event-type tag
          <p class="c-mod-calender__links"><a href="{external}">TITLE</a></p>
          <p class="c-txt-caption-01">開場 18:00／開演 19:00 ...</p>

The page embeds the current + next month as two JS-switchable tabs; both
are fully present in the raw HTML, so one fetch yields both. Appending
?ym= is ignored by the site — there is no deeper pagination and no way to
see 3+ months out, so scrape() fetches the single URL and reads each
tab-body's own month heading.

Facts-only / no detail pass: title links point to THIRD-PARTY promoter or
ticketing sites (event-td.com, diskgarage, clarismusic, starto.jp, ...),
NOT an internal venue detail page. They are stored as the event's
ticket_url and never scraped (aggregator hard-rule). Everything the
aggregator keeps — title, date, event-type, OPEN/START — is already on
this one listing page, so supports_detail is False.

Multi-day runs list each performance-day as its own <tr> with its own
showtimes and share one external ticket URL; the per-day source_url is the
internal schedule URL + "#YYYY-MM-DD" so dedupe keys stay unique
(yokohama_arena precedent). No prices are published (ticketing is off-site).

Mixed-calendar policy: the site's own c-txt-tag__item type label drives
the category — コンサート/イベント (concerts + artist/idol-led events at a
music hall) are MUSIC, その他 (misc) is OTHER; tu.is_nonmusic on the title
is a safety net that forces OTHER for anything clearly non-concert. Rows
that are pure venue holds (an event tag but NO ticket link, e.g. the
"Reserved" placeholder) are the venue's non-public business and skipped.
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

# The site's own event-type tag -> our category. Kanadevia is a music/
# concert hall, so its "イベント" rows are artist/idol-led (fan events,
# anniversary lives); keep them MUSIC. "その他" is the venue's misc bucket.
TAG_CATEGORY = {
    "コンサート": Category.MUSIC,   # concert
    "イベント": Category.MUSIC,     # (artist/idol-led) event
    "その他": Category.OTHER,       # other / misc
}

_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_DAY_RE = re.compile(r"(\d{1,2})")
_KAIJO_RE = re.compile(r"開場\s*(\d{1,2}:\d{2})")   # OPEN
_KAIEN_RE = re.compile(r"開演\s*(\d{1,2}:\d{2})")   # START


class KanadeviaHallScraper(BaseScraper):
    source_id = "kanadevia_hall"
    source_name = "Kanadevia Hall"
    BASE = "https://www.tokyo-dome.co.jp"
    SCHEDULE_URL = "https://www.tokyo-dome.co.jp/tdc-hall/event/"
    supports_detail = False        # no internal detail page; links are 3rd-party

    VENUE = dict(
        venue_name="Kanadevia Hall",
        venue_area="Suidobashi",
        address="1-3-61 Koraku, Bunkyo-ku, Tokyo (Tokyo Dome City)",
        lat=35.7057, lng=139.7524,
    )

    def scrape(self) -> Iterable[Event]:
        # One fetch: the page carries the current + next month inline.
        yield from self.parse(self.fetch(self.SCHEDULE_URL))

    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for body in soup.select("div.c-mod-tab__body"):
            heading = body.select_one("p.c-ttl-set-calender")
            if not heading:
                continue
            mm = _MONTH_RE.search(heading.get_text(strip=True))
            if not mm:
                continue
            year, month = int(mm.group(1)), int(mm.group(2))
            for row in body.select("tr.c-mod-calender__item"):
                ev = self._parse_row(row, year, month)
                if ev and ev.source_url not in events:
                    events[ev.source_url] = ev
        return list(events.values())

    def _parse_row(self, row, year: int, month: int) -> Event | None:
        # Empty calendar day (no scheduled event).
        if not row.select_one("div.c-mod-calender__detail-in"):
            return None

        # Title comes ONLY from the ticket/promoter anchor. A content row
        # with no anchor (e.g. "Reserved") is a venue hold -> skip loudly
        # rather than emit a titleless placeholder.
        anchor = row.select_one("p.c-mod-calender__links a[href]")
        if not anchor:
            return None
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if not title:
            return None
        ticket_url = urljoin(self.SCHEDULE_URL, anchor["href"])

        day_span = row.select_one("span.c-mod-calender__day")
        if not day_span:
            return None
        dm = _DAY_RE.search(day_span.get_text(strip=True))
        if not dm:
            return None
        try:
            date = dt.date(year, month, int(dm.group(1))).isoformat()
        except ValueError:
            return None

        detail_text = row.select_one("td.c-mod-calender__detail").get_text(
            " ", strip=True)
        km = _KAIJO_RE.search(detail_text)
        sm = _KAIEN_RE.search(detail_text)
        open_time = km.group(1) if km else None
        start_time = sm.group(1) if sm else None

        tag = row.select_one("span.c-txt-tag__item")
        tag_text = tag.get_text(strip=True) if tag else ""
        category = TAG_CATEGORY.get(tag_text, Category.MUSIC)
        if tu.is_nonmusic(title):
            category = Category.OTHER

        # Multi-day runs reuse the same external ticket URL; the internal
        # schedule URL + #date keeps per-performance dedupe keys unique.
        source_url = f"{self.SCHEDULE_URL}#{date}"

        return Event(
            source=self.source_id, source_url=source_url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(detail_text)),
            ticket_url=ticket_url,
            tags=[tag_text] if tag_text else [],
            **self.VENUE,
        )
