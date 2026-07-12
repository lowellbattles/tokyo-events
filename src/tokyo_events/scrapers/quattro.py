"""Scraper for CLUB QUATTRO (Parco group) — https://www.club-quattro.com

Halls: shibuya (in-scope), plus umeda/nagoya/hiroshima (out of geography).
There is no Kawasaki Quattro (roadmap note was wrong — hall nav verified
live 2026-07-12: shibuya/umeda/nagoya/hiroshima only).

Month pages: /{hall}/schedule/?ym={YYYYMM}; bare /{hall}/schedule/ is the
current month. Event detail links carry the id in the query string
(/{hall}/schedule/detail/?cd=018163) — do NOT strip the query.

Each listing <li> carries data-event-date="YYYY-MM-DD" (semantic data
attribute; day-number text + month context is the fallback). Block text:
"01 WED.  {lineup}  {title}  開場/開演 16:45 / 17:30  料金 前売 ￥5,800 /
当日 ￥6,300 ...". The flyer <img alt> duplicates the title.
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

HALLS = {
    "quattro_shibuya": dict(
        slug="shibuya", venue_name="Shibuya CLUB QUATTRO",
        venue_area="Shibuya", address="32-13 Udagawa-cho, Shibuya-ku, Tokyo",
        lat=35.661378, lng=139.696716),
}

DETAIL_HREF_RE = re.compile(r"/schedule/detail/\?cd=\d+")
KAIJO_KAIEN_RE = re.compile(
    r"開場\s*/\s*開演\D{0,6}(\d{1,2}:\d{2})\s*/\s*(\d{1,2}:\d{2})")
DAY_DOW_RE = re.compile(
    r"^\s*(\d{1,2})\s*(SUN|MON|TUE|WED|THU|FRI|SAT)\.?\s*", re.I)
PRICE_ZONE_RE = re.compile(r"料金(.{0,200})", re.S)


class QuattroScraper(BaseScraper):
    source_name = "CLUB QUATTRO"
    BASE = "https://www.club-quattro.com"

    def __init__(self, hall_id: str = "quattro_shibuya",
                 months_ahead: int = 3, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown Quattro hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        base = f"{self.BASE}/{self.hall['slug']}/schedule/"
        yield from self.parse(self.fetch(base), month=first)
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            try:
                html = self.fetch(f"{base}?ym={m.year}{m.month:02d}")
            except RuntimeError:
                break   # months that far out often don't exist yet
            yield from self.parse(html, month=m)

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            url = urljoin(self.BASE, a["href"])   # keep ?cd= query
            block = a.find_parent("li") or a
            ev = self._parse_block(block, url, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, block, url: str, month: dt.date | None,
                     today: dt.date | None) -> Event | None:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))

        date = None
        iso = block.get("data-event-date") if hasattr(block, "get") else None
        if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
            date = iso
        else:                       # fallback: "01 WED." + month context
            m = DAY_DOW_RE.match(text)
            if m:
                day = int(m.group(1))
                if month is not None:
                    try:
                        date = dt.date(month.year, month.month,
                                       day).isoformat()
                    except ValueError:
                        date = None
                else:
                    date = tu.infer_year(dt.date.today().month, day, today)
        if not date:
            return None

        # Title: the flyer image alt duplicates it; fall back to the text
        # between the day prefix and the 開場 marker.
        title = None
        for img in block.find_all("img", alt=True):
            alt = re.sub(r"\s+", " ", img["alt"]).strip()
            if alt:
                title = alt
                break
        body = DAY_DOW_RE.sub("", text)
        head = re.split(r"開場|OPEN", body, maxsplit=1)[0].strip()
        if not title:
            title, _ = tu.split_repeated_title(head)
        if not title:
            return None

        # Lineup: block text before the title, slash-separated artists.
        lineup: list[str] = []
        cut = head.find(title)
        if cut > 0:
            lineup = [s.strip() for s in re.split(r"[/／]", head[:cut])
                      if s.strip() and "：" not in s]

        m = KAIJO_KAIEN_RE.search(text)
        open_time, start_time = (m.group(1), m.group(2)) if m else (None, None)

        price_text, price_min, is_free = (None, None, None)
        pz = PRICE_ZONE_RE.search(text)
        if pz:
            zone = re.split(r"ドリンク|お問い合わせ|TEL", pz.group(1))[0]
            price_text, price_min, is_free = tu.parse_prices(zone)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
            venue_name=self.hall["venue_name"],
            venue_area=self.hall["venue_area"],
            address=self.hall["address"],
            lat=self.hall["lat"], lng=self.hall["lng"],
        )
