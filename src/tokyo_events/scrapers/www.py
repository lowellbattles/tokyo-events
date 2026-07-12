"""Scraper for WWW / WWW X (Shibuya, Udagawa-chō) — https://www-shibuya.jp

Both halls share one schedule page; each event <article> carries
data-place="www" or "www_x" (semantic data attribute; the visible
"WWW X" / "WWW" place label in the block text is the fallback).

Current month at /schedule/, other months at /schedule/{YYYYMM}.php.
Event pages at /schedule/{id}.php where id is a zero-padded event number
(019828) — distinguishable from month pages (20xxxx).

Block text: "01 Wed  {title}  {subtitle}  OPEN / START 18:30 / 19:30"
— note TWO times after the combined marker (open then start), unlike
the single-time "OPEN/START 23:00" convention textutils handles.
No prices on the listing; the detail pass fills them.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

HALLS = {
    "www": dict(venue_name="WWW", place="www"),
    "www_x": dict(venue_name="WWW X", place="www_x"),
}
VENUE_COMMON = dict(
    venue_area="Shibuya", address="13-17 Udagawa-cho, Shibuya-ku, Tokyo",
    lat=35.660557, lng=139.696371)

EVENT_HREF_RE = re.compile(r"/schedule/(\d+)\.php$")
MONTH_ID_RE = re.compile(r"^20\d{4}$")          # 202608.php = month page
OPEN_START_PAIR_RE = re.compile(
    r"OPEN\s*/\s*START\W*(\d{1,2}:\d{2})\s*/\s*(\d{1,2}:\d{2})")
DAY_DOW_RE = re.compile(
    r"^\s*(\d{1,2})\s*(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\b\.?\s*", re.I)


class WWWScraper(BaseScraper):
    source_name = "WWW Shibuya"
    BASE = "https://www-shibuya.jp"

    def __init__(self, hall_id: str, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown WWW hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        yield from self.parse(self.fetch(f"{self.BASE}/schedule/"),
                              month=first)
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            try:
                html = self.fetch(
                    f"{self.BASE}/schedule/{m.year}{m.month:02d}.php")
            except RuntimeError:
                break
            yield from self.parse(html, month=m)

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            m = EVENT_HREF_RE.search(a["href"])
            if not m or MONTH_ID_RE.match(m.group(1)):
                continue
            url = a["href"] if a["href"].startswith("http") \
                else self.BASE + a["href"]
            block = a.find_parent("article") or a
            if not self._is_own_hall(block):
                continue
            ev = self._parse_block(block, url, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _is_own_hall(self, block) -> bool:
        place = block.get("data-place") if hasattr(block, "get") else None
        if place:
            return place == self.hall["place"]
        # Fallback: visible place label. "WWW X" contains "WWW", so test
        # for the X label first.
        text = block.get_text(" ", strip=True)
        is_x = bool(re.search(r"WWW\s*X\b", text))
        return is_x if self.hall["place"] == "www_x" else not is_x

    def _parse_block(self, block, url: str, month: dt.date | None,
                     today: dt.date | None) -> Event | None:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        m = DAY_DOW_RE.match(text)
        if not m:
            return None
        day = int(m.group(1))
        if month is not None:
            try:
                date = dt.date(month.year, month.month, day).isoformat()
            except ValueError:
                return None
        else:
            date = tu.infer_year(dt.date.today().month, day, today)
        if not date:
            return None

        heading = block.find(["h1", "h2", "h3", "h4"])
        title = (re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
                 if heading else None)
        body = DAY_DOW_RE.sub("", text)
        head = re.split(r"OPEN", body, maxsplit=1)[0].strip()
        if not title:
            title, _ = tu.split_repeated_title(head)
        if not title:
            return None
        # Subtitle: text between the title and the OPEN marker.
        subtitle = None
        cut = head.find(title)
        if cut >= 0:
            rest = head[cut + len(title):].strip(" -|・")
            subtitle = rest or None

        tm = OPEN_START_PAIR_RE.search(text)
        if tm:
            open_time, start_time = tm.group(1), tm.group(2)
        else:
            open_time, start_time = tu.parse_times(text)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, subtitle=subtitle,
            category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
            venue_name=self.hall["venue_name"], **VENUE_COMMON,
        )
