"""Scraper family for the Shibuya O-Group (Spotify venues)
— https://shibuya-o.com

One class covers O-EAST / O-WEST / O-Crest / O-nest: shared WordPress
platform, schedules at /{hall}/schedule/ with per-event pages at
/{hall}/schedule/{slug}/.

Event slugs carry no date, so dates come from listing-block text
(e.g. "2026.7.3 fri" / "7.3(金)"); year is inferred when omitted.

NOTE: built from a rendered-text capture of the O-EAST site (2026-07-02).
Validate against saved raw HTML on first live run.
"""

from __future__ import annotations

import re
from typing import Iterable
import datetime as dt

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

HALLS = {
    "oeast": dict(slug="east", venue_name="Spotify O-EAST",
                  lat=35.660585, lng=139.695591),
    "owest": dict(slug="west", venue_name="Spotify O-WEST",
                  lat=35.660367, lng=139.695041),
    "ocrest": dict(slug="crest", venue_name="Spotify O-Crest",
                   lat=35.660585, lng=139.695591),
    "onest": dict(slug="nest", venue_name="Spotify O-nest",
                  lat=35.660367, lng=139.695041),
}
PRICE_BLOCK_RE = re.compile(r"(?:ADV|前売)\s*(.{0,200})", re.I | re.S)


class OGroupScraper(BaseScraper):
    source_name = "Shibuya O-Group"
    BASE = "https://shibuya-o.com"

    def __init__(self, hall_id: str, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown O-Group hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self._href_re = re.compile(
            rf"/{self.hall['slug']}/schedule/[^/]+/?$")

    def scrape(self) -> Iterable[Event]:
        html = self.fetch(f"{self.BASE}/{self.hall['slug']}/schedule/")
        yield from self.parse(html)

    def parse(self, html: str, today: dt.date | None = None, **context
              ) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0]
            if not self._href_re.search(href):
                continue
            url = href if href.startswith("http") else self.BASE + href
            # The event block is either the anchor itself (listing wraps
            # everything in <a>) or its enclosing card.
            block = a
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
            if len(text) < 12 or not tu.parse_date(text, today):
                parent = a.find_parent(["article", "li", "div"])
                if parent is not None:
                    block, text = parent, re.sub(
                        r"\s+", " ", parent.get_text(" ", strip=True))
            ev = self._parse_block(block, url, text, today)
            if ev:
                prev = events.get(ev.source_url)
                if not prev or len(text) > getattr(prev, "_richness", 0):
                    ev._richness = len(text)  # type: ignore[attr-defined]
                    events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, block, url: str, text: str,
                     today: dt.date | None) -> Event | None:
        date = tu.parse_date(text, today)
        if not date:
            return None

        heading = block.find(["h1", "h2", "h3", "h4", "h5"])
        if heading and heading.get_text(strip=True):
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
        else:
            head = re.split(r"OPEN|ADV|前売", text, maxsplit=1)[0]
            head = tu.FULL_DATE_RE.sub("", head)
            head = tu.MONTH_DAY_RE.sub("", head)
            head = re.sub(r"^[\s()（）./a-z]{0,8}", "", head, flags=re.I)
            title, _ = tu.split_repeated_title(head.strip())
        if not title:
            return None

        open_time, start_time = tu.parse_times(text)
        price_text, price_min, is_free = (None, None, None)
        m = PRICE_BLOCK_RE.search(text)
        if m:
            price_text, price_min, is_free = tu.parse_prices(m.group(1))

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
            venue_name=self.hall["venue_name"], venue_area="Shibuya",
            lat=self.hall["lat"], lng=self.hall["lng"],
        )
