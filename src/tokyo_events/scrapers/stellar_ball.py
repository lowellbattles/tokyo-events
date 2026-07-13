"""Scraper for Stellar Ball (ステラボール) — Shinagawa Prince Hotel.

The official schedule lives inside the Prince Hotels corporate site:
https://www.princehotels.co.jp/shinagawa/stellarball/  (fully static; the
candidate stellarball.com domain does NOT resolve — do not use it).

The whole schedule is one ``<section id=schedule>`` Swiper carousel with one
``<div class=swiper-slide>`` per month, and every month is already present in
the raw HTML on a single fetch (no ``?ym=`` month pagination exists). Each
month slide carries its year+month in ``<p class="gfont num">2026.7</p>``; each
event is a ``<div class=box>`` whose ``.txt`` holds::

    <p class=date>7.10 Fri.</p>                              (single day)
    <p class=date>6.26 Fri. - 7.5 Sun.</p>休演日：6.29 Mon.    (range + dark day)
    <p class=event>{title, may contain <br>}</p>
    <a>＞オフィシャルサイト</a> and/or <a>＞詳細はこちら</a>       (outbound links)

plus an optional SNS ``<ul class=lyt-sns-list>`` of image-only icon links.

Facts this source can and cannot give:
- The venue's own page never prints OPEN/START times or ¥ prices (grepped:
  zero matches), so those fields stay ``None``.
- There is NO venue-owned per-event detail page — each event links out to a
  different promoter/ticket domain — so ``supports_detail`` is ``False`` and the
  one or two outbound links are kept as ``ticket_links`` (provider "official"
  for the promoter site, the vendor name for a ticket page).
- The whole schedule shares one URL, so ``source_url`` gets a ``#YYYY-MM-DD``
  fragment for per-event dedupe uniqueness (yokohama_arena precedent).

The parser keys off the text conventions (the ``20YY.M`` month header, ``M.D``
date shapes, ``休演日：`` dark-day marker, ``＞`` link labels), not on class
names; if the ``#schedule`` section is missing it returns 0 events (loud).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(
    venue_name="ステラボール",
    venue_area="Shinagawa",
    address="4-10-30 Takanawa, Minato-ku, Tokyo",
    lat=35.6277449, lng=139.735559,
)

# "2026.7" (year + month) inside a month slide's head.
NUM_HEADER_RE = re.compile(r"(20\d{2})\D{1,3}(\d{1,2})")
# "7.10", "6.26" — a month.day token (day-of-week / year text won't match).
MD_RE = re.compile(r"(\d{1,2})\.(\d{1,2})")
# "休演日：6.29" dark-day markers (a sibling text node, not inside <p class=date>).
DARKDAY_RE = re.compile(r"休演日[：:]\s*(\d{1,2})\.(\d{1,2})")


class StellarBallScraper(BaseScraper):
    source_id = "stellar_ball"
    source_name = "Stellar Ball"
    BASE = "https://www.princehotels.co.jp"
    URL = "https://www.princehotels.co.jp/shinagawa/stellarball/"
    #: the listing is terminal — no venue-owned detail page to enrich against
    supports_detail = False

    def scrape(self) -> Iterable[Event]:
        yield from self.parse(self.fetch(self.URL))

    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        section = soup.find("section", id="schedule")
        if section is None:
            return []                    # structure churned -> loud (0 events)
        events: dict[str, Event] = {}
        for slide in section.select("div.swiper-slide"):
            hyear, hmonth = self._slide_year_month(slide)
            if hyear is None:
                continue                 # month header unreadable -> skip slide
            for box in slide.select("div.box"):
                ev = self._parse_box(box, hyear, hmonth)
                if ev is None:
                    continue
                url = ev.source_url
                if url in events:        # same-day double-booking -> keep both
                    i = 2
                    while f"{url}-{i}" in events:
                        i += 1
                    url = f"{url}-{i}"
                    ev.source_url = url
                events[url] = ev
        return list(events.values())

    # ------------------------------------------------------------------
    def _slide_year_month(self, slide) -> tuple[int | None, int | None]:
        """(year, month) from the slide's ``2026.7`` header. Reads the head
        text, not a class name, so it survives markup churn."""
        head = slide.select_one("div.head") or slide
        m = NUM_HEADER_RE.search(head.get_text(" ", strip=True))
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    def _parse_box(self, box, hyear: int, hmonth: int) -> Event | None:
        date_p = box.select_one("p.date")
        event_p = box.select_one("p.event")
        if date_p is None or event_p is None:
            return None
        md = MD_RE.findall(date_p.get_text(" ", strip=True))
        if not md:
            return None
        start = self._mk_date(int(md[0][0]), int(md[0][1]), hyear, hmonth)
        if start is None:
            return None
        end = None
        if len(md) >= 2:
            end = self._mk_date(int(md[-1][0]), int(md[-1][1]), hyear, hmonth)
            if end and end < start:      # range crossed a year boundary
                end = self._mk_date(int(md[-1][0]), int(md[-1][1]),
                                    hyear + 1, hmonth)
            if end is not None and end <= start:
                end = None

        title = re.sub(r"\s+", " ", event_p.get_text(" ", strip=True)).strip()
        if not title:
            return None

        # Dark days (休演日) are facts but the Event schema has no exclusion
        # field, so record them as informational tags (frontend ignores tags).
        tags: list[str] = []
        for dmo, dda in DARKDAY_RE.findall(box.get_text(" ", strip=True)):
            iso = self._mk_date(int(dmo), int(dda), hyear, hmonth)
            if iso is not None:
                tags.append(f"休演日:{iso.isoformat()}")

        cat = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC
        url = f"{self.URL}#{start.isoformat()}"
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=cat,
            start_date=start.isoformat(),
            end_date=end.isoformat() if end else None,
            ticket_links=self._outbound_links(box), tags=tags,
            **VENUE,
        )

    def _mk_date(self, month: int, day: int, hyear: int,
                 hmonth: int) -> dt.date | None:
        """Resolve a year for a bare month/day using the slide's header month
        as the anchor (an event may start in the month before its slide, e.g.
        a 6.26-7.5 run under the July header; Dec/Jan wraps are handled too)."""
        diff = month - hmonth
        year = hyear - 1 if diff > 6 else hyear + 1 if diff < -6 else hyear
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    def _outbound_links(self, box) -> list[dict]:
        """The event's outbound official/ticket links (image-only SNS icon
        links are skipped). Stored as ticket_links per the venue having no
        times/prices/tickets of its own."""
        links, seen = [], set()
        for a in box.find_all("a", href=True):
            text = a.get_text(strip=True)
            if not text:                 # SNS icons wrap an <img>, no text
                continue
            href = a["href"].strip()
            if not href.startswith("http"):
                href = urljoin(self.URL, href)
            if href in seen:
                continue
            seen.add(href)
            links.append({"provider": self._provider(href, text),
                          "url": href, "code": None})
        return links

    @staticmethod
    def _provider(href: str, text: str) -> str:
        host = urlparse(href).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        for domain, provider in tu.TICKET_PROVIDERS.items():
            if domain in host:
                return provider
        if "diskgarage" in host:
            return "diskgarage"
        if "オフィシャル" in text or "official" in text.lower():
            return "official"
        return host.split(".")[0] if host else "link"
