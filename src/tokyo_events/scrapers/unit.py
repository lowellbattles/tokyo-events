"""Scraper for 代官山UNIT (Daikanyama UNIT) — https://www.unit-tokyo.com

~600-cap club / live house. WordPress + "The Events Calendar" plugin, but
the schedule is fully server-rendered static HTML (no JS needed).

Month pages: /schedule/YYYY-MM/ (e.g. /schedule/2026-08/); the bare
/schedule/ serves the current month. Prev/next nav carries data-month
attributes but month-walking by YYYY-MM is enough. The month grid also
shows a few spillover days from the adjacent months, so the listing's own
MM/DD is authoritative for the month — only the YEAR is inferred from the
page's month context (add_months walk).

Listing card (div.p-schedule__item) carries: MM/DD date, weekday, an OPEN
time, an optional title-top / title-bottom around the main h2 title, and a
slash-separated lineup. START time, price tiers (¥N,NNN in a table.price)
and outbound playguide links (e+ etc.) live on the detail page
/schedule/{id}/ — parse_detail pulls them.

Do NOT trust the detail page's schema.org JSON-LD clock time: the probe
found its startDate hour is a placeholder (08:00) that disagrees with the
displayed OPEN. Times come from the p-detail__open-door dt/dd text.

Scope: UNIT (the main hall) only. The co-located SALOON floor is a
separate domain (saloon-tokyo.com) and is out of scope for this source.
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
    venue_name="代官山UNIT",
    venue_area="Daikanyama",
    address="東京都渋谷区恵比寿西1-34-17 ザ・ハウスビル B1F",
    lat=None, lng=None,
)

# event detail links look like /schedule/14494/ (numeric id, no query)
DETAIL_HREF_RE = re.compile(r"/schedule/(\d+)/?$")
MMDD_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")


class UnitScraper(BaseScraper):
    source_id = "unit_daikanyama"
    source_name = "UNIT (Daikanyama)"
    BASE = "https://www.unit-tokyo.com"

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ------------------------------------------------------------- fetching
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        # current month is served by the bare /schedule/ page
        for ev in self.parse(self.fetch(f"{self.BASE}/schedule/"), month=first):
            if ev.source_url not in seen:
                seen.add(ev.source_url)
                yield ev
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/schedule/{m.year}-{m.month:02d}/"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break               # far-future months may 404
            page = self.parse(html, month=m)
            if not page:
                break               # empty month -> no more scheduled shows
            for ev in page:
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # ---------------------------------------------------------- pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for item in soup.select("div.p-schedule__item"):
            a = item.find("a", href=True)
            if not a or not DETAIL_HREF_RE.search(a["href"].split("?")[0]):
                continue
            ev = self._parse_item(item, a["href"], month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_item(self, item, href: str, month: dt.date | None,
                    today: dt.date | None) -> Event | None:
        url = urljoin(self.BASE, href.split("?")[0].split("#")[0])

        date_text = _sel_text(item, ".p-schedule__item-date")
        mm = MMDD_RE.search(date_text or "")
        if not mm:
            return None                     # no date -> not a real card
        start_date = _resolve_date(int(mm.group(1)), int(mm.group(2)),
                                   month, today)
        if not start_date:
            return None

        title = _compose_title(
            _sel_text(item, ".p-schedule__item-title-top"),
            _sel_text(item, ".p-schedule__item-title"),
            _sel_text(item, ".p-schedule__item-title-bottom"))
        if not title:
            return None

        open_time = None
        om = TIME_RE.search(_sel_text(item, ".p-schedule__item-open") or "")
        if om:
            open_time = om.group(1)

        lineup = _split_lineup(_sel_text(item, ".p-schedule__item-lineup"))

        # UNIT is a single-purpose live house, so this is effectively always
        # MUSIC; still gate through the shared non-music guard for safety.
        blob = f"{title} {' '.join(lineup)}"
        category = Category.OTHER if tu.is_nonmusic(blob) else Category.MUSIC

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=start_date,
            open_time=open_time, lineup=lineup,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(blob)),
            **VENUE,
        )

    # -------------------------------------------------------- detail enrich
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill START time, ¥ price tiers and playguide links from an
        event's own /schedule/{id}/ page. Keys off the site's structured
        open-door dl and price table, not the (unreliable) JSON-LD time."""
        soup = BeautifulSoup(html, "lxml")

        open_time = start_time = None
        price_blobs: list[str] = []
        dl = soup.select_one("dl.p-detail__open-door")
        if dl:
            for div in dl.find_all("div", recursive=False):
                dt_el, dd_el = div.find("dt"), div.find("dd")
                if not dt_el or not dd_el:
                    continue
                label = dt_el.get_text(" ", strip=True).upper()
                dd_text = dd_el.get_text(" ", strip=True)
                if "OPEN" in label:
                    m = TIME_RE.search(dd_text)
                    open_time = m.group(1) if m else open_time
                elif "START" in label:
                    m = TIME_RE.search(dd_text)
                    start_time = m.group(1) if m else start_time
                else:                        # ADV / DOOR / 料金 / 前売 / 当日
                    price_blobs.append(dd_text)

        if open_time and not ev.open_time:
            ev.open_time = open_time
        if start_time and not ev.start_time:
            ev.start_time = start_time

        # Prices: prefer the venue's own price table(s); fall back to the
        # price dd text. Never scan the whole page (artist bios, merch).
        if ev.price_min is None:
            if not price_blobs:
                price_blobs = [t.get_text(" ", strip=True)
                               for t in soup.select("table.price")]
            if price_blobs:
                ptext, pmin, is_free = tu.parse_prices(
                    tu.strip_drink_charges(" ".join(price_blobs)))
                if pmin is not None:
                    ev.price_text, ev.price_min, ev.is_free = ptext, pmin, is_free

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(
                soup.get_text(" ", strip=True)):
            ev.is_sold_out = True
        return ev


# --------------------------------------------------------------- helpers
def _sel_text(node, selector: str) -> str | None:
    el = node.select_one(selector)
    return el.get_text(" ", strip=True) if el else None


def _compose_title(top: str | None, main: str | None,
                   bottom: str | None) -> str:
    """Join the site's title-top / h2 title / title-bottom lines into one
    title string, dropping empties and collapsing whitespace."""
    parts = [p for p in (top, main, bottom) if p and p.strip()]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _split_lineup(text: str | None) -> list[str]:
    """Lineup is slash-separated (/ or ／). NOT ｜, which the site uses to
    pair a name with its reading (e.g. 'YELLOW 黃宣｜イエロー...')."""
    if not text:
        return []
    return [s.strip() for s in re.split(r"[/／]", text) if s.strip()]


def _resolve_date(mm: int, dd: int, month: dt.date | None,
                  today: dt.date | None) -> str | None:
    """MM/DD is authoritative for the month; infer only the year. When a
    page-month anchor is known, pick the year whose date sits closest to
    that month (handles Dec/Jan grid spillover). Otherwise fall back to the
    forward-looking heuristic in textutils."""
    if month is None:
        return tu.infer_year(mm, dd, today)
    ref = dt.date(month.year, month.month, 15)
    best: tuple[int, dt.date] | None = None
    for year in (month.year - 1, month.year, month.year + 1):
        try:
            cand = dt.date(year, mm, dd)
        except ValueError:
            continue
        dist = abs((cand - ref).days)
        if best is None or dist < best[0]:
            best = (dist, cand)
    return best[1].isoformat() if best else None
