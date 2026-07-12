"""Scraper for duo MUSIC EXCHANGE (Shibuya) — https://www.duomusicexchange.com

Month pages at /schedule/{YYYY}/index_{YYYY}-{MM}.html (the /schedule/
landing page itself is a stale archive — link straight to month pages).
One <section id="daybox"> per show, preceded by <a name="{YYMMDD}"> day
anchors; there are no per-event detail pages, so the source_url is the
month page + day anchor and supports_detail is off.

Block text: "01 WED.  {artist}  {title}  OPEN/START 18:00 / 19:00
ADV./DOOR 【オールスタンディング】ADV.¥7,700-(税込) ..." — two times after
OPEN/START, prices in the ADV./DOOR zone.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(venue_name="duo MUSIC EXCHANGE", venue_area="Shibuya",
             address="2-14-8 Dogenzaka, Shibuya-ku, Tokyo",
             lat=35.657219, lng=139.696330)

OPEN_START_PAIR_RE = re.compile(
    r"OPEN\s*/\s*START\W*(\d{1,2}:\d{2})\s*/\s*(\d{1,2}:\d{2})")
DAY_DOW_RE = re.compile(
    r"^\s*(\d{1,2})\s*(SUN|MON|TUE|WED|THU|FRI|SAT)\.?\s*", re.I)
ADV_ZONE_RE = re.compile(r"ADV\.?\s*/\s*DOOR(.{0,250})", re.I | re.S)


class DuoScraper(BaseScraper):
    source_id = "duo"
    source_name = "duo MUSIC EXCHANGE"
    BASE = "https://www.duomusicexchange.com"
    supports_detail = False       # day anchors only, no event pages

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def month_url(self, m: dt.date) -> str:
        return (f"{self.BASE}/schedule/{m.year}/"
                f"index_{m.year}-{m.month:02d}.html")

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            try:
                html = self.fetch(self.month_url(m))
            except RuntimeError:
                if i == 0:
                    raise           # current month missing = loud failure
                break
            yield from self.parse(html, month=m)

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        page_url = self.month_url(month) if month else f"{self.BASE}/schedule/"
        events: dict[str, Event] = {}
        for box in soup.find_all("section", id="daybox"):
            text = re.sub(r"\s+", " ", box.get_text(" ", strip=True))
            m = DAY_DOW_RE.match(text)
            if not m:
                continue
            day = int(m.group(1))
            if month is not None:
                try:
                    date = dt.date(month.year, month.month, day).isoformat()
                except ValueError:
                    continue
            else:
                date = tu.infer_year(dt.date.today().month, day, today)
            if not date:
                continue

            anchor = box.find_previous("a", attrs={"name": True})
            frag = anchor["name"] if anchor else f"{date.replace('-', '')[2:]}"
            url = f"{page_url}#{frag}"

            body = DAY_DOW_RE.sub("", text)
            head = re.split(r"OPEN\s*/\s*START|OPEN|開場", body,
                            maxsplit=1)[0].strip()
            title, subtitle = tu.split_repeated_title(head)
            if not title:
                continue

            tm = OPEN_START_PAIR_RE.search(text)
            open_time, start_time = ((tm.group(1), tm.group(2)) if tm
                                     else tu.parse_times(text))
            price_text, price_min, is_free = (None, None, None)
            pz = ADV_ZONE_RE.search(text)
            if pz:
                zone = re.split(r"ドリンク|Ticket|受付", pz.group(1))[0]
                price_text, price_min, is_free = tu.parse_prices(zone)

            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, subtitle=subtitle,
                category=Category.MUSIC, start_date=date,
                open_time=open_time, start_time=start_time,
                price_text=price_text, price_min=price_min, is_free=is_free,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(text)), **VENUE,
            )
            if ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())
