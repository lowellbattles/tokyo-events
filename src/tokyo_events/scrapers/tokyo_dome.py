"""Scraper for 東京ドーム (Tokyo Dome) — https://www.tokyo-dome.co.jp/dome/

Tokyo Dome is the ~55,000-cap stadium ("Big Egg") in Tokyo Dome City,
operated by Tokyo Dome Corp — the same operator (and same CMS) as the
sibling Kanadevia Hall scraper, so the calendar markup is identical.
The official schedule lives at /dome/event/schedule.html as a single
static, server-side-rendered page that carries a FULL YEAR of confirmed
events inline (the site note: "現時点で確定しているイベント（1年分）の
情報のみ掲載されます" — only confirmed events, one year, are shown).

Structure (keys off text conventions + stable c-mod-calender__* classes):
  <div class="c-mod-tab__body">              one per month tab
    <p class="c-ttl-set-calender">2026年07月</p>
    <table class="c-mod-calender">
      <tr class="c-mod-calender__item">       one per calendar DAY
        <th><span class="c-mod-calender__day">04</span>
            <span class="c-mod-calender__day">(土)</span></th>
        <td class="c-mod-calender__detail">
          <div class="c-mod-calender__detail-in">   one per event on that day
            <span class="c-txt-tag__item">コンサート</span>   event-type tag
            <p class="c-mod-calender__links"><a href="{external}">TITLE</a></p>
            <p class="c-txt-caption-01">開場 15:30／開演 17:30</p>

Every month section (data-tab-head=YYYYMM) is present in the raw HTML;
the tabs are only a JS view toggle. One GET returns everything — there is
no ?ym=/?month= pagination and no JSON endpoint — so scrape() fetches the
single URL and reads each tab-body's own YYYY年MM月 heading for the
year+month, plus the day number from each row.

Mixed-calendar policy: Tokyo Dome is primarily a BASEBALL stadium (home of
the Yomiuri Giants). The site's own c-txt-tag__item type label drives what
we keep: only コンサート rows become events (as Category.MUSIC). 野球
(baseball games / TOKYO DOME TOUR) and non-concert イベント rows are the
dome's own business, not public music events, and are skipped entirely.
tu.is_nonmusic on the title is a safety net that forces Category.OTHER for
anything clearly non-concert that still slipped in under a コンサート tag.

Facts-only / no detail pass: title links point to THIRD-PARTY artist or
promoter sites (top4-event.com, yoasobi-music.jp, livenationhip.co.jp,
starto.jp, ...), NOT an internal venue detail page — there is no per-event
page on tokyo-dome.co.jp to enrich from. They are stored as the event's
ticket_url and never scraped (aggregator hard-rule). Everything kept —
title, date, event-type, OPEN/START — is already on this one listing page,
so supports_detail is False. Tokyo Dome never publishes ticket prices.

Multi-day runs list each performance-day as its own <tr> (often with
different showtimes, e.g. a matinee on day 2), sharing one external ticket
URL; the per-day source_url is the internal schedule URL + "#YYYY-MM-DD" so
dedupe keys stay unique (kanadevia_hall / yokohama_arena precedent).
Genuine concerts whose tickets are not yet on sale appear as コンサート
rows with a plain-text title and no anchor (e.g. "＝LOVE in TOKYO DOME");
those are kept as facts with ticket_url=None.
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

# The site's own event-type tag. Tokyo Dome is a stadium whose daily
# business is baseball, so — unlike the music-hall sibling — ONLY コンサート
# rows are treated as events; every other tag is skipped.
CONCERT_TAG = "コンサート"

_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_DAY_RE = re.compile(r"(\d{1,2})")
_KAIJO_RE = re.compile(r"開場\s*(\d{1,2}:\d{2})")   # OPEN
_KAIEN_RE = re.compile(r"開演\s*(\d{1,2}:\d{2})")   # START


class TokyoDomeScraper(BaseScraper):
    source_id = "tokyo_dome"
    source_name = "Tokyo Dome"
    BASE = "https://www.tokyo-dome.co.jp"
    SCHEDULE_URL = "https://www.tokyo-dome.co.jp/dome/event/schedule.html"
    supports_detail = False        # no internal detail page; links are 3rd-party

    VENUE = dict(
        venue_name="東京ドーム",
        venue_area="Suidobashi",
        address="1-3-61 Koraku, Bunkyo-ku, Tokyo (Tokyo Dome City)",
        lat=35.70558, lng=139.75195,
    )

    def scrape(self) -> Iterable[Event]:
        # One fetch: the single schedule page carries a full year inline.
        yield from self.parse(self.fetch(self.SCHEDULE_URL))

    def parse(self, html: str, **context) -> list[Event]:
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
                for ev in self._parse_row(row, year, month):
                    if ev.source_url not in events:
                        events[ev.source_url] = ev
        return list(events.values())

    def _parse_row(self, row, year: int, month: int) -> list[Event]:
        # Day number lives in the row header; one date, possibly several
        # event blocks (a dome day can list a tour + a game).
        day_span = row.select_one(
            "th.c-mod-calender__title span.c-mod-calender__day")
        if not day_span:
            return []
        dm = _DAY_RE.search(day_span.get_text(strip=True))
        if not dm:
            return []
        try:
            date = dt.date(year, month, int(dm.group(1))).isoformat()
        except ValueError:
            return []

        out: list[Event] = []
        for block in row.select("div.c-mod-calender__detail-in"):
            ev = self._parse_block(block, date)
            if ev is not None:
                out.append(ev)
        return out

    def _parse_block(self, block, date: str) -> Event | None:
        # Keep ONLY the site's own コンサート tag; baseball (野球) and
        # non-concert イベント rows are the stadium's business, not events.
        tag = block.select_one("span.c-txt-tag__item")
        tag_text = tag.get_text(strip=True) if tag else ""
        if tag_text != CONCERT_TAG:
            return None

        links = block.select_one("p.c-mod-calender__links")
        if links is None:
            return None
        # Title = anchor text when tickets are on sale, else the plain-text
        # placeholder for a confirmed-but-not-yet-ticketed concert.
        title = re.sub(r"\s+", " ", links.get_text(" ", strip=True)).strip()
        if not title:
            return None
        anchor = links.find("a", href=True)
        ticket_url = urljoin(self.SCHEDULE_URL, anchor["href"]) if anchor else None

        block_text = block.get_text(" ", strip=True)
        km = _KAIJO_RE.search(block_text)
        sm = _KAIEN_RE.search(block_text)
        open_time = km.group(1) if km else None
        start_time = sm.group(1) if sm else None

        category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC

        # Each performance-day is its own event; the internal schedule URL +
        # #date keeps dedupe keys unique across a multi-day run.
        source_url = f"{self.SCHEDULE_URL}#{date}"

        return Event(
            source=self.source_id, source_url=source_url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(block_text)),
            ticket_url=ticket_url,
            tags=[tag_text],
            **self.VENUE,
        )
