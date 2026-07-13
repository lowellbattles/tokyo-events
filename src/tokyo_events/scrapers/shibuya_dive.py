"""Scraper for SHIBUYA DIVE — https://shibuya-dive.com

A small idol-leaning live house near the Shibuya/Ebisu border (WordPress +
Events Manager plugin). The bare /schedule/ page renders the CURRENT month
as clean static HTML; other months load via ?date=YYYY-MM, and the sidebar
(ul.sidebar-month-list > a.sidebar-month-link) enumerates every future month
that actually has bookings — scrape() walks those rather than blindly
iterating empty months.

Each event = one <article class="schedule-article">: a full date in
p.schedule-date ("YYYY.MM.DD"), title in h3.schedule-article-ttl, the detail
href on a.schedule-link, and an OPEN/START/ADV/ACT table. The venue uses the
time labels loosely (an ADV row was seen holding a *time*, not a price), and
prices never appear in the listing at all — so times are read only from the
literal OPEN/START <th> labels, ¥ values are parsed only where a ¥ sign is
present, and the generic detail pass fills price tiers + ticket links. ACT is
a ／-separated lineup.

Structural-failure guard: events key off article.schedule-article blocks and
per-event date/title text; if that structure churns, parse() returns 0 events
(loud) rather than emitting garbage.
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

VENUE = dict(
    venue_name="SHIBUYA DIVE", venue_area="Shibuya",
    address="東京都渋谷区東2-22-5 シブロジ 1F/B1",
    lat=35.6528, lng=139.7078,
)

DATE_RE = re.compile(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
MONTH_PARAM_RE = re.compile(r"date=(\d{4})-(\d{1,2})")


class ShibuyaDiveScraper(BaseScraper):
    source_id = "shibuya_dive"
    source_name = "SHIBUYA DIVE"
    BASE = "https://shibuya-dive.com"

    def __init__(self, months_ahead: int = 6, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        base = f"{self.BASE}/schedule/"
        html = self.fetch(base)
        yield from self.parse(html)
        # The sidebar lists every future month that actually has bookings
        # (the current month is dropped by _month_links to avoid re-fetching
        # what the bare page already served).
        for url in self._month_links(html)[: self.months_ahead]:
            try:
                yield from self.parse(self.fetch(url))
            except RuntimeError:
                break   # a listed month that 404s: stop walking further out

    def _month_links(self, html: str) -> list[str]:
        """Future-month URLs from the sidebar, excluding the month the bare
        page already rendered (identified by the calendar's td.month_name)."""
        soup = BeautifulSoup(html, "lxml")
        active = None
        cell = soup.find(class_="month_name")
        if cell:
            m = re.search(r"(20\d{2})\.(\d{1,2})", cell.get_text())
            if m:
                active = f"{int(m.group(1))}-{int(m.group(2)):02d}"
        links, seen = [], set()
        for a in soup.select("a.sidebar-month-link[href]"):
            m = MONTH_PARAM_RE.search(a["href"])
            if not m:
                continue
            ym = f"{int(m.group(1))}-{int(m.group(2)):02d}"
            if ym == active or ym in seen:
                continue
            seen.add(ym)
            links.append(urljoin(self.BASE, a["href"]))
        return links

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for art in soup.select("article.schedule-article"):
            ev = self._parse_article(art)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_article(self, art) -> Event | None:
        a = art.find("a", href=True)
        if not a:
            return None
        url = urljoin(self.BASE, a["href"].split("#")[0])
        if "shibuya-dive.com" not in url or "/schedule/" not in url:
            return None

        # Date: prefer p.schedule-date ("YYYY.MM.DD"); fall back to the first
        # full date anywhere in the block (text convention, not CSS).
        dbox = art.find(class_="schedule-date")
        date_src = (dbox.get_text(" ", strip=True) if dbox
                    else art.get_text(" ", strip=True))
        m = DATE_RE.search(date_src)
        if not m:
            return None
        try:
            date = dt.date(int(m.group(1)), int(m.group(2)),
                           int(m.group(3))).isoformat()
        except ValueError:
            return None

        ttl = art.find(class_="schedule-article-ttl") or art.find("h3")
        title = re.sub(r"\s+", " ", ttl.get_text(" ", strip=True)).strip() \
            if ttl else None
        if not title:
            return None

        open_time = start_time = None
        price_text = price_min = is_free = None
        lineup: list[str] = []
        for row in art.select("table tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            label = th.get_text(" ", strip=True).upper()
            val = td.get_text(" ", strip=True)
            if label == "OPEN":
                tm = TIME_RE.search(val)
                if tm:
                    open_time = tm.group(1)
            elif label == "START":
                tm = TIME_RE.search(val)
                if tm:
                    start_time = tm.group(1)
            elif label == "ACT":
                lineup = [s.strip() for s in re.split(r"[／/]", val)
                          if s.strip()]
            elif "¥" in val or "￥" in val:
                # The venue keeps ¥ tiers off the listing, but if one ever
                # appears here, parse it faithfully (only when a ¥ is present
                # so an ADV row holding a bare time is never mis-read).
                price_text, price_min, is_free = tu.parse_prices(val)

        cat = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=cat, start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            price_text=price_text, price_min=price_min, is_free=is_free,
            **VENUE,
        )
