"""Scraper family for Billboard Live — https://www.billboard-live.com

Covers TOKYO (Roppongi/Midtown) and YOKOHAMA (Bashamichi); OSAKA exists on
the same platform if geography ever widens.

Structure verified live 2026-07-02, re-verified 2026-07-16 after a relayout:
- Month schedule pages: /{city}/schedules?month=YYYY-MM-01
- Event anchors: /{city}/show?event_id=ev-NNNNN&date=YYYY-MM-DD
  (event id AND date in the URL — best-case scrapeability)
- Each anchor carries a semantic <hgroup> heading: <h3 aria-label="TITLE">
  plus <p> subtitle/reading lines. Title comes from there (semantic tags,
  not CSS classes). Flattened block text still supplies
  "1st Stage / Open HH:MM / Start HH:MM - 2nd Stage / ..." and
  "Music Charge" price tiers (min tier = casual seat).
- 2026-07 relayout: the date token moved in FRONT of the title and gained
  spaces — "2026 7.16 ( Thu )" — which the old text-only title parse
  swallowed into title_ja. The text parse survives as a fallback only.
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
DATE_TOKEN_RE = re.compile(
    r"20\d{2}\s*(\d{1,2})[.](\d{1,2})\s*[(（]\s*\w+\s*[)）]")


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
            month = tu.add_months(first, i)
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
            title, subtitle = self._heading(a)
            ev = self._parse_block(text, url, date[0], title, subtitle)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    @staticmethod
    def _heading(a) -> tuple[str | None, str | None]:
        """Title/subtitle from the anchor's semantic heading: <hgroup> with
        <h3 aria-label="full title"> and <p> subtitle/reading lines."""
        hg = a.find("hgroup")
        h3 = hg.find("h3") if hg else a.find("h3")
        if h3 is None:
            return None, None
        title = (h3.get("aria-label") or "").strip() \
            or re.sub(r"\s+", " ", h3.get_text(" ", strip=True))
        subs = [re.sub(r"\s+", " ", p.get_text(" ", strip=True))
                for p in (hg.find_all("p") if hg else [])]
        subtitle = " / ".join(s for s in subs if s) or None
        return title or None, subtitle

    def _parse_block(self, text: str, url: str, date: str,
                     title: str | None = None,
                     subtitle: str | None = None) -> Event | None:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            return None

        # Text-convention fallback when no semantic heading was found:
        # pre-relayout blocks double the title before the date token and put
        # the reading after it; post-relayout blocks lead with the date token
        # and follow with the title.
        if not title:
            head = re.split(r"Open\s*/\s*Start", text, maxsplit=1)[0]
            m = DATE_TOKEN_RE.search(head)
            if m:
                before, after = head[:m.start()].strip(), head[m.end():].strip()
                if before:
                    title, _ = tu.split_repeated_title(before)
                    subtitle = after.replace(title, "").strip(" -–—|・※") or None
                else:
                    title, subtitle = tu.split_repeated_title(after)
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
