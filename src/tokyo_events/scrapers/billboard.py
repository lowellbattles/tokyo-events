"""Scraper family for Billboard Live — https://www.billboard-live.com

Covers TOKYO (Roppongi/Midtown) and YOKOHAMA (Bashamichi); OSAKA exists on
the same platform if geography ever widens.

Structure verified live 2026-07-02:
- Month schedule pages: /{city}/schedules?month=YYYY-MM-01
- Event anchors: /{city}/show?event_id=ev-NNNNN&date=YYYY-MM-DD
  (event id AND date in the URL — best-case scrapeability)
- Block text: doubled title, date "2026 6.1(Mon)", artist reading,
  "1st Stage / Open HH:MM / Start HH:MM - 2nd Stage / ...",
  "Music Charge" price tiers (min tier = casual seat).
- Multi-night runs share event_id with distinct date params -> one Event
  per night, which is what we want.

Genre prior: Billboard Live programming skews jazz/soul/city-pop and
international touring acts -> default genres reflect that and get
corrected in review when wrong.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

CITIES = {
    "billboard_tokyo": dict(
        city="tokyo", venue_name="Billboard Live TOKYO",
        venue_area="Roppongi",
        address="Tokyo Midtown Garden Terrace 4F, 9-7-4 Akasaka, Minato-ku",
        lat=35.665651, lng=139.730723),
    "billboard_yokohama": dict(
        city="yokohama", venue_name="Billboard Live YOKOHAMA",
        venue_area="Bashamichi", address=None,
        lat=35.450602, lng=139.633331),
}
SHOW_HREF_RE = re.compile(r"/show\?")
STAGE_RE = re.compile(
    r"1st\s*Stage\s*/\s*Open\s*(\d{1,2}:\d{2})\s*/\s*Start\s*(\d{1,2}:\d{2})",
    re.I)
STAGE2_RE = re.compile(r"2nd\s*Stage", re.I)
DATE_TOKEN_RE = re.compile(r"20\d{2}\s*(\d{1,2})[.](\d{1,2})\s*[(（]\w+[)）]")


class BillboardScraper(BaseScraper):
    source_name = "Billboard Live"
    BASE = "https://www.billboard-live.com"
    supports_detail = False   # listing already carries times/prices/tiers

    def __init__(self, club_id: str, months_ahead: int = 2, **kw):
        super().__init__(**kw)
        if club_id not in CITIES:
            raise ValueError(f"unknown Billboard club: {club_id}")
        self.club = CITIES[club_id]
        self.source_id = club_id
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            month = _add_months(first, i)
            url = (f"{self.BASE}/{self.club['city']}/schedules"
                   f"?month={month:%Y-%m-01}")
            yield from self.parse(self.fetch(url))

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not SHOW_HREF_RE.search(href):
                continue
            qs = parse_qs(urlparse(href).query)
            event_id, date = qs.get("event_id"), qs.get("date")
            if not (event_id and date):
                continue
            url = href if href.startswith("http") else self.BASE + href
            text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            ev = self._parse_block(text, url, date[0])
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, text: str, url: str, date: str) -> Event | None:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            return None

        # Title: text before the date token is a doubled title; text between
        # the date token and "Open / Start" holds title+reading (subtitle).
        head = re.split(r"Open\s*/\s*Start", text, maxsplit=1)[0]
        m = DATE_TOKEN_RE.search(head)
        if m:
            before, after = head[:m.start()].strip(), head[m.end():].strip()
            title, _ = tu.split_repeated_title(before)
            subtitle = after.replace(title, "").strip(" -–—|・※") or None
        else:
            title, subtitle = tu.split_repeated_title(head.strip())
        if not title:
            return None

        stage = STAGE_RE.search(text)
        open_time, start_time = (stage.group(1), stage.group(2)) if stage \
            else tu.parse_times(text)
        tags = ["2-stages"] if STAGE2_RE.search(text) else []

        price_zone = text.split("Music Charge", 1)[-1]
        price_text, price_min, is_free = tu.parse_prices(price_zone)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, subtitle=subtitle,
            category=Category.MUSIC, genres=["jazz-soul"],
            start_date=date, open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)), tags=tags,
            venue_name=self.club["venue_name"],
            venue_area=self.club["venue_area"],
            address=self.club["address"],
            lat=self.club["lat"], lng=self.club["lng"],
        )


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1)
