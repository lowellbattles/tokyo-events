"""Scraper family for the Loft Project group — https://www.loft-prj.co.jp

Halls: Shinjuku LOFT (/schedule/loft/), Shimokitazawa SHELTER
(/schedule/shelter/). Same platform, slightly different event-link
shapes: LOFT uses /schedule/loft/schedule/{id}, SHELTER uses
/schedule/shelter/{id} — the href pattern accepts both.

Block text: "2026 07 12 Sunday  “{title}”  OPEN 13:30 - START 14:30
{lineup...}" — full space-separated date (FULL_DATE_RE handles it),
standard OPEN/START, title usually in 「」/“” quotes. Prices are on the
detail pages, which the detail pass fills.
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
    "loft_shinjuku": dict(
        slug="loft", venue_name="Shinjuku LOFT", venue_area="Kabukicho",
        address="1-12-9 Kabukicho B2, Shinjuku-ku, Tokyo",
        lat=35.696330, lng=139.703533),
    "shelter": dict(
        slug="shelter", venue_name="下北沢SHELTER", venue_area="Shimokitazawa",
        address="2-6-10 Kitazawa B1, Setagaya-ku, Tokyo",
        lat=35.662398, lng=139.667800),
}

QUOTED_TITLE_RE = re.compile(r"[「“『]([^」”』]{2,120})[」”』]")
DOW_RE = re.compile(
    r"\b(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\b", re.I)


class LoftScraper(BaseScraper):
    source_name = "Loft Project"
    BASE = "https://www.loft-prj.co.jp"

    def __init__(self, hall_id: str, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown Loft hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self._href_re = re.compile(
            rf"/schedule/{self.hall['slug']}/(?:schedule/)?(\d+)/?$")

    def scrape(self) -> Iterable[Event]:
        html = self.fetch(f"{self.BASE}/schedule/{self.hall['slug']}/")
        yield from self.parse(html)

    def parse(self, html: str, today: dt.date | None = None, **context
              ) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not self._href_re.search(a["href"].split("?")[0]):
                continue
            url = a["href"] if a["href"].startswith("http") \
                else self.BASE + a["href"]
            block = a.find_parent(["article", "li", "section", "div"]) or a
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
            date = tu.parse_date(text, today)
            if not date:
                # block may be the anchor itself with the card as parent
                parent = block.find_parent(["article", "li", "div"])
                if parent is not None:
                    block = parent
                    text = re.sub(r"\s+", " ",
                                  parent.get_text(" ", strip=True))
                    date = tu.parse_date(text, today)
            if not date:
                continue

            # Title: quoted span if present, else text between the
            # day-of-week word and the OPEN marker.
            title = None
            qm = QUOTED_TITLE_RE.search(text)
            head = re.split(r"OPEN|開場", text, maxsplit=1)[0]
            dm = DOW_RE.search(head)
            plain = head[dm.end():].strip() if dm else head.strip()
            if qm and (not plain or qm.group(1) in plain
                       or plain in qm.group(0)):
                title = plain or qm.group(1)
            else:
                title = plain
            if not title:
                continue
            title = re.sub(r"\s+", " ", title).strip()

            open_time, start_time = tu.parse_times(text)
            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, category=Category.MUSIC, start_date=date,
                open_time=open_time, start_time=start_time,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
                venue_name=self.hall["venue_name"],
                venue_area=self.hall["venue_area"],
                address=self.hall["address"],
                lat=self.hall["lat"], lng=self.hall["lng"],
            )
            prev = events.get(ev.source_url)
            if not prev or len(text) > getattr(prev, "_richness", 0):
                ev._richness = len(text)  # type: ignore[attr-defined]
                events[ev.source_url] = ev
        return list(events.values())
