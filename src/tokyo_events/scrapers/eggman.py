"""Scraper for Shibuya eggman — http://eggman.jp (small ~350-cap live house).

The venue splits its calendar into two WordPress category archives that are
BOTH scraped for full coverage under the single source id "eggman":
  - /schedule-cat/daytime/    regular live-house gigs (idol / indie / j-rock,
                              in-store release events)
  - /schedule-cat/nighttime/  late-night club / DJ programming (OPEN 23:00-24:xx)
Same markup, same month pagination: ?syear=YYYY&smonth=MM.

IMPORTANT: this host only serves plain HTTP — https://eggman.jp/* fails TLS
hostname verification. All URLs stay on http:// deliberately; do not "upgrade".

Each event is a self-contained ``<article class="scheduleList">``. The full
date is split between the page-level month header (``div.monthHeader h1`` ->
"2026.07") and each event's day cell (``time strong`` -> "03"); the parser
combines them. Times/prices live in ``div.scheListBody ul li`` rows labelled
with plain-ASCII ``<small>OPEN/START/ADV/DOOR/TICKET</small>`` tags (never the
kanji 開場/開演 on this template). Price shows up in two shapes — structured
ADV/DOOR rows ("4400yen+1D") or a freeform ``li.other`` blob ("全自由：¥5,500
/ U-22：¥3,500", "一般3,400 / 学生2,400", "3000+1D") — so the price parser
accepts ¥, the "yen"/"円" suffix, comma-grouped bare numbers, and plain
club-night entry integers. Lineup sits in ``div.act``; club nights sometimes
put an "ENTRY SITE" reservation link there instead of artist names.
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

# Month header text, e.g. "2026.07".
MONTH_HEADER_RE = re.compile(r"(20\d{2})\s*[.．]\s*(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")

# Price amounts. Ordered branches: ¥N,NNN | Nyen/N円 | comma-grouped bare |
# plain 3-5 digit entry fee. The bare branch guards against times/drink codes
# by refusing a leading/trailing ':' or digit (so "1D", "18:30" never leak).
PRICE_NUM_RE = re.compile(
    r"[¥￥]\s*(\d[\d,]*)"
    r"|(\d[\d,]*)\s*(?:yen|円)"
    r"|(\d{1,3}(?:,\d{3})+)"
    r"|(?<![:\d])(\d{3,5})(?![:\d])",
    re.I,
)
FREE_RE = re.compile(r"入場無料|無料|FREE\s*(?:ENTRY|LIVE)?", re.I)
# Pure group label like 【ARTIST】 / [COMEDIAN] that is not an artist name.
GROUP_LABEL_RE = re.compile(r"^[【\[][^】\]]*[】\]]$")


def _amounts(text: str) -> list[int]:
    out: list[int] = []
    for m in PRICE_NUM_RE.finditer(text):
        g = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if g:
            try:
                out.append(int(g.replace(",", "")))
            except ValueError:
                pass
    return out


def _provider_for(href: str) -> str:
    for domain, prov in tu.TICKET_PROVIDERS.items():
        if domain in href:
            return prov
    return "other"


class EggmanScraper(BaseScraper):
    source_id = "eggman"
    source_name = "Shibuya eggman"
    BASE = "http://eggman.jp"
    CATEGORIES = ("daytime", "nighttime")

    VENUE = dict(
        venue_name="Shibuya eggman",
        venue_area="Shibuya",
        address="1-6-8 Jinnan B1, Shibuya-ku, Tokyo 150-0041",
        lat=None, lng=None,
    )

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # --- fetching ---------------------------------------------------------
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for cat in self.CATEGORIES:
            base = f"{self.BASE}/schedule-cat/{cat}/"
            # current month (bare URL)
            for ev in self.parse(self.fetch(base)):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev
            # future months; stop the category on the first empty / all-seen
            # page (guards against the archive clamping to the current month).
            for i in range(1, self.months_ahead):
                m = tu.add_months(first, i)
                url = f"{base}?syear={m.year}&smonth={m.month:02d}"
                try:
                    html = self.fetch(url)
                except RuntimeError:
                    break
                fresh = [ev for ev in self.parse(html, month=m)
                         if ev.source_url not in seen]
                if not fresh:
                    break
                for ev in fresh:
                    seen.add(ev.source_url)
                    yield ev

    # --- pure parse -------------------------------------------------------
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")

        # The page's own month header is authoritative; the ``month`` kwarg is
        # only a fallback pin for tests / clamped pages.
        page_month = month
        header = soup.select_one("div.monthHeader h1")
        if header:
            hm = MONTH_HEADER_RE.search(header.get_text(strip=True))
            if hm:
                page_month = dt.date(int(hm.group(1)), int(hm.group(2)), 1)

        events: dict[str, Event] = {}
        for art in soup.find_all("article", class_="scheduleList"):
            ev = self._parse_article(art, page_month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_article(self, art, month: dt.date | None,
                       today: dt.date | None) -> Event | None:
        # --- date: month header + day cell ---
        day_el = art.select_one("time strong")
        if day_el is None:
            return None
        day_txt = day_el.get_text(strip=True)
        if not day_txt.isdigit():
            return None
        day = int(day_txt)
        if month is not None:
            try:
                date = dt.date(month.year, month.month, day).isoformat()
            except ValueError:
                return None
        else:
            return None  # no month context -> can't date it (loud: skipped)

        # --- title + detail url ---
        link = art.select_one("div.scheListHeader h1 a[href]")
        if link is None:
            return None
        url = urljoin(self.BASE, link["href"])
        title = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        if not title:
            img = art.select_one("div.scheListImg img[alt]")
            if img and img.get("alt"):
                title = re.sub(r"\s+", " ", img["alt"]).strip()
        if not title:
            return None

        # --- times + prices from the labelled <li> rows ---
        open_time = start_time = None
        price_parts: list[str] = []
        for li in art.select("div.scheListBody ul li"):
            txt = re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
            small = li.find("small")
            label = small.get_text(strip=True).upper() if small else ""
            if label == "OPEN":
                mt = TIME_RE.search(txt)
                if mt:
                    open_time = mt.group(1)
            elif label == "START":
                mt = TIME_RE.search(txt)
                if mt:
                    start_time = mt.group(1)
            elif txt:
                price_parts.append(txt)

        price_text = price_min = is_free = None
        if price_parts:
            joined = " / ".join(price_parts)
            amounts = _amounts(joined)
            price_text = joined[:300]
            if amounts:
                price_min = min(amounts)
                is_free = price_min == 0
            elif FREE_RE.search(joined):
                price_min, is_free = 0, True

        # --- lineup + any reservation links from div.act ---
        lineup, ticket_links = self._parse_act(art)

        title_norm = f"{title} {' '.join(lineup)}"
        category = Category.OTHER if tu.is_nonmusic(title_norm) else Category.MUSIC

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, genres=[],
            start_date=date, open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(art.get_text(" ", strip=True))),
            lineup=lineup, ticket_links=ticket_links,
            venue_name=self.VENUE["venue_name"],
            venue_area=self.VENUE["venue_area"],
            address=self.VENUE["address"],
            lat=self.VENUE["lat"], lng=self.VENUE["lng"],
        )

    @staticmethod
    def _parse_act(art) -> tuple[list[str], list[dict]]:
        act = art.select_one("div.act")
        if act is None:
            return [], []
        ticket_links: list[dict] = []
        seen_href: set[str] = set()
        # Reservation / "ENTRY SITE" links live inside the act block on club
        # nights; capture the URL as a ticket link and drop it from the text so
        # it is not mistaken for an artist name.
        for a in act.find_all("a", href=True):
            href = a["href"]
            if href not in seen_href:
                seen_href.add(href)
                ticket_links.append(
                    {"provider": _provider_for(href), "url": href, "code": None})
            a.decompose()
        for br in act.find_all("br"):
            br.replace_with("\n")
        raw = act.get_text("\n", strip=True)

        lineup: list[str] = []
        for chunk in re.split(r"[／/\n]", raw):
            name = re.sub(r"\s+", " ", chunk).strip(" 　・|-–—").strip()
            if not name or GROUP_LABEL_RE.match(name):
                continue
            if name not in lineup:
                lineup.append(name)
        return lineup, ticket_links
