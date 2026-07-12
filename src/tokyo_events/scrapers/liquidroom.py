"""Scraper for LIQUIDROOM (Ebisu) — https://www.liquidroom.net

Structure observed (June 2026):
- Month listing pages at /schedule/YYYY/MM
- Each event is an <a> whose href matches /schedule/{slug}_{YYYYMMDD}
  (occasionally with a -N suffix), wrapping the full event block.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

EVENT_HREF_RE = re.compile(r"/schedule/[^/]+_(\d{8})(?:-\d+)?/?$")
DAY_PREFIX_RE = re.compile(r"^\s*(\d{1,2})\s*(SUN|MON|TUE|WED|THU|FRI|SAT)\s*", re.I)
PRICE_BLOCK_RE = re.compile(r"ADV\s*(.+?)(?:LINE\s*UP|INFO|SOLD\s*OUT|$)", re.S)
LINEUP_RE = re.compile(r"LINE\s*UP\s*(.+?)(?:INFO|$)", re.S)


class LiquidroomScraper(BaseScraper):
    source_id = "liquidroom"
    source_name = "LIQUIDROOM"
    BASE = "https://www.liquidroom.net"
    VENUE = dict(
        venue_name="LIQUIDROOM",
        venue_area="Ebisu",
        address="3-16-6 Higashi, Shibuya-ku, Tokyo",
        lat=35.649044,
        lng=139.710580,
    )

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        today = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            month = _add_months(today, i)
            html = self.fetch(f"{self.BASE}/schedule/{month:%Y/%m}")
            yield from self.parse(html)

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            m = EVENT_HREF_RE.search(a["href"])
            if not m:
                continue
            url = a["href"]
            if url.startswith("/"):
                url = self.BASE + url
            text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if "OPEN" not in text and "ADV" not in text:
                continue  # nav/teaser link
            ev = self._parse_block(a, url, text, m.group(1))
            if ev:
                prev = events.get(ev.source_url)
                if not prev or len(text) > getattr(prev, "_richness", 0):
                    ev._richness = len(text)  # type: ignore[attr-defined]
                    events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, a_tag, url: str, text: str, yyyymmdd: str
                     ) -> Event | None:
        try:
            date = dt.datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()
        except ValueError:
            return None

        body = DAY_PREFIX_RE.sub("", text)

        head = re.split(r"OPEN|ADV", body, maxsplit=1)[0].strip()
        heading = a_tag.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading and heading.get_text(strip=True):
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
            subtitle = head.replace(title, "").strip(" -–—|・") or None
        else:
            title, subtitle = tu.split_repeated_title(head)

        open_time, start_time = tu.parse_times(body)
        price_text, price_min, is_free = (None, None, None)
        raw_price = tu.first(PRICE_BLOCK_RE, body)
        if raw_price:
            price_text, price_min, is_free = tu.parse_prices(raw_price)

        lineup_raw = tu.first(LINEUP_RE, body)
        lineup = []
        if lineup_raw:
            lineup = [s.strip() for s in re.split(r"[/／]", lineup_raw)
                      if s.strip() and "順" not in s]

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title or None, subtitle=subtitle or None,
            category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
            lineup=lineup, **self.VENUE,
        )


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1)
