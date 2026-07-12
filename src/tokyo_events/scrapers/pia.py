"""Scrapers for Pia-operated venues: Toyosu PIT and ぴあアリーナMM.

Both are run by PIA Corporation but on *different* site generations
(hypothesis of a shared platform was wrong — verified 2026-07-02):

Toyosu PIT — https://toyosu.pia-pit.jp
  Full listing lives at /schedule-list/index.html with relative links
  ../schedule/{YYYYMM}/{id}.html (YYYYMM = posting month, NOT the event
  month, so dates come from block text + year inference). The front page
  only carries a slider with a reversed "DAY M.D TITLE" format — don't
  scrape it. Listing text: "MM.DD DAY TITLE ..." — no times/prices on
  the index, the detail pass fills them.

Pia Arena MM — https://pia-arena-mm.jp
  Month pages: /event@p1={YYYY}&p2={MM}.html
  Event links: /event/{id}.html with text "MM.DD DAY TITLE".
  Hall-rental days are titled "PRIVATE" and must be skipped.
  No times/prices on the index -> detail pass.
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

_MMDD_DAY_RE = re.compile(
    r"^\s*(\d{1,2})[.](\d{1,2})\s*(SUN|MON|TUE|WED|THU|FRI|SAT)\s*", re.I)


class ToyosuPitScraper(BaseScraper):
    source_id = "toyosu_pit"
    source_name = "Toyosu PIT"
    BASE = "https://toyosu.pia-pit.jp"
    LIST_URL = "https://toyosu.pia-pit.jp/schedule-list/index.html"
    # Live links are relative ("../schedule/202605/8527.html"), so no
    # leading-slash anchor; (?:^|/) keeps e.g. "reschedule/" from matching.
    HREF_RE = re.compile(r"(?:^|/)schedule/\d{6}/\d+\.html$")
    VENUE = dict(venue_name="Toyosu PIT", venue_area="Toyosu",
                 address="6-1-23 Toyosu, Koto-ku, Tokyo",
                 lat=35.649623, lng=139.792071)

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        yield from self.parse(self.fetch(self.LIST_URL))
        # Future months live at /schedule-list/{YYYY}/{M}/index.html
        # (month not zero-padded). A missing far-future page is normal,
        # not a structure failure — stop quietly there.
        first = dt.date.today().replace(day=1)
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/schedule-list/{m.year}/{m.month}/index.html"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            yield from self.parse(html, page_url=url)

    def parse(self, html: str, today: dt.date | None = None,
              page_url: str | None = None, **context) -> list[Event]:
        page_url = page_url or self.LIST_URL
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not self.HREF_RE.search(a["href"]):
                continue
            url = urljoin(page_url, a["href"])
            text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            block = a
            if not tu.parse_date(text, today):
                parent = a.find_parent(["article", "li", "div"])
                if parent is not None:
                    block = parent
                    text = re.sub(r"\s+", " ",
                                  parent.get_text(" ", strip=True))
            ev = self._parse_block(block, url, text, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, block, url, text, today) -> Event | None:
        date = tu.parse_date(text, today)
        if not date:
            return None
        # Card text reads "MM.DD DAY  EVENT TITLE  ARTIST"; the heading
        # element holds the ARTIST (not the title), so strip it off the
        # tail and keep it as lineup.
        heading = block.find(["h1", "h2", "h3", "h4", "h5"])
        artist = (re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
                  if heading else None)
        head = _MMDD_DAY_RE.sub("", text)
        head = re.split(r"OPEN|開場", head, maxsplit=1)[0].strip()
        if artist and head.endswith(artist) and head != artist:
            head = head[: -len(artist)].strip()
        title, subtitle = tu.split_repeated_title(head)
        if not title:
            title = artist
        if not title:
            return None
        open_time, start_time = tu.parse_times(text)
        return Event(
            source=self.source_id, source_url=url, title_ja=title,
            subtitle=subtitle, category=Category.MUSIC, start_date=date,
            open_time=open_time, start_time=start_time,
            lineup=[artist] if artist and artist != title else [],
            is_sold_out=bool(tu.SOLD_OUT_RE.search(text)), **self.VENUE,
        )


class PiaArenaMMScraper(BaseScraper):
    source_id = "pia_arena_mm"
    source_name = "ぴあアリーナMM"
    BASE = "https://pia-arena-mm.jp"
    # Live links are relative ("event/6690.html") — no leading slash.
    HREF_RE = re.compile(r"(?:^|/)event/\d+\.html$")
    VENUE = dict(venue_name="ぴあアリーナMM", venue_area="Minatomirai",
                 address="3-2-2 Minatomirai, Nishi-ku, Yokohama",
                 lat=35.460199, lng=139.628839)

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            month = tu.add_months(first, i)
            url = f"{self.BASE}/event@p1={month.year}&p2={month:%m}.html"
            yield from self.parse(self.fetch(url), month=month)

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not self.HREF_RE.search(a["href"]):
                continue
            url = urljoin(f"{self.BASE}/", a["href"])
            text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            m = _MMDD_DAY_RE.match(text)
            if not m:
                continue
            body = text[m.end():].strip()
            if not body or body.upper().startswith("PRIVATE"):
                continue   # hall rental day — not a public event
            mo, day = int(m.group(1)), int(m.group(2))
            if month is not None:
                try:
                    date = dt.date(month.year, mo, day).isoformat()
                except ValueError:
                    continue
            else:
                date = tu.infer_year(mo, day, today)
            if not date:
                continue
            title, subtitle = tu.split_repeated_title(body)
            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, subtitle=subtitle,
                category=(Category.OTHER if tu.is_nonmusic(body)
                          else Category.MUSIC), start_date=date,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(text)),
                **self.VENUE,
            )
            if ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())
