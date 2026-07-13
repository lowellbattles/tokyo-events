"""Scraper family for the Zepp hall chain — https://www.zepp.co.jp

One class covers every hall: schedule pages live at
/hall/{slug}/schedule/ and share a platform. Event blocks contain a
date, title, [OPEN]/[START] times and [PRICE] tiers, with a detail link
matching /hall/{slug}/schedule/single/?rid=NNNNNN.

NOTE: built from a rendered-text capture of the DiverCity schedule page
(2026-07-02). On first live run, save one raw HTML page into
tests/fixtures/ and adjust selectors if the block-walking heuristic
misses fields. The parser fails loudly (0 events) on structural change.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

DETAIL_HREF_RE = re.compile(r"/schedule/single/\?rid=\d+")

HALLS = {
    "zepp_divercity": dict(
        slug="divercity", venue_name="Zepp DiverCity (TOKYO)",
        venue_area="Odaiba", lat=35.625034, lng=139.775442),
    "zepp_haneda": dict(
        slug="haneda", venue_name="Zepp Haneda (TOKYO)",
        venue_area="Haneda", lat=35.548923, lng=139.742870),
    "zepp_shinjuku": dict(
        slug="shinjuku", venue_name="Zepp Shinjuku (TOKYO)",
        venue_area="Shinjuku", lat=35.695479, lng=139.701572),
    "zepp_yokohama": dict(
        slug="yokohama", venue_name="KT Zepp Yokohama",
        venue_area="Yokohama", lat=35.462391, lng=139.630783),
}


class ZeppScraper(BaseScraper):
    source_name = "Zepp"
    BASE = "https://www.zepp.co.jp"

    def __init__(self, hall_id: str, months_ahead: int = 6, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown Zepp hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        # Month pages live on the same URL with ?_y=YYYY&_m=M (M unpadded);
        # the site's month nav holds ~12 forward months. Walk the whole
        # months_ahead window: card dates carry their own year, so an empty
        # or clamped page can't mis-date anything, and interior empty months
        # (hall maintenance) must not hide later bookings.
        base = f"{self.BASE}/hall/{self.hall['slug']}/schedule/"
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = base if i == 0 else f"{base}?_y={m.year}&_m={m.month}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            yield from fresh

    def parse(self, html: str, today: dt.date | None = None, **context
              ) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            url = a["href"]
            if url.startswith("/"):
                url = self.BASE + url
            block = _enclosing_block(a)
            if block is None:
                continue
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
            ev = self._parse_block(block, url, text, today)
            if ev and ev.source_url not in events:
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
            head = re.split(r"\[?OPEN", text, maxsplit=1)[0]
            head = tu.MONTH_DAY_RE.sub("", tu.FULL_DATE_RE.sub("", head))
            title, _ = tu.split_repeated_title(head.strip(" )）("))
        if not title:
            return None

        open_time, start_time = tu.parse_times(text)
        m = re.search(r"\[?PRICE[\]】]?(.{0,300})", text, re.I | re.S)
        price_text, price_min, is_free = tu.parse_prices(
            m.group(1) if m else text)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
            venue_name=self.hall["venue_name"],
            venue_area=self.hall["venue_area"],
            lat=self.hall["lat"], lng=self.hall["lng"],
        )


def _enclosing_block(a_tag, max_hops: int = 5):
    """Walk up from the detail link until the container holds OPEN/START
    info — resilient to class-name changes in the theme."""
    node = a_tag
    for _ in range(max_hops):
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if "OPEN" in text.upper() and len(text) > 30:
            return node
        node = node.parent
    return None
