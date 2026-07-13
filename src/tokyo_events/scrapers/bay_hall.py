"""Scraper for Yokohama Bay Hall — https://bayhall.jp

A ~1,000-cap live house in Shinyamashita, Naka-ku, Yokohama. Fully static,
server-rendered WordPress HTML (no JS needed for event data).

Listing pages group one month of events under a single `<h2>MM Month YYYY</h2>`
heading, one `<article id="post{ID}">` per event carrying date + title +
detail URL only (times/prices live on the detail page):

  <article id="post9428">
    <a href="https://bayhall.jp/schedule/2026/07/9428/">
      <div class="date">04<span>Sat</span></div>
      <h3>DAISY TOWN</h3>
      ...preview prose (ignored — facts only)...

Pagination: bare /schedule/ is the current month; older/newer months live at
/{YYYY}/{MM}/ date archives (NOT /schedule/{YYYY}/{MM}/ — that prefix is the
individual event-detail namespace). The site keeps ~3 months back and ~7
forward live simultaneously.

Two site-specific quirks handled here:
  1. Rental/private bookings render as `class="private"` with `<h3>PRIVATE</h3>`
     and no public event info — skipped entirely (venue's own business, not an
     event we can list).
  2. Cancelled shows keep a normal article but append 【公演中止】 (or 延期 =
     postponed) to the <h3> title — recorded as a `cancelled`/`postponed` tag
     with the marker stripped from the clean title (better for artist matching).

Detail pages hold everything in label-keyed `<dl><dt>LABEL</dt><dd>value</dd>`
blocks (keyed on dt text, not CSS classes):
  OPEN / START -> "OPEN 15:00 / CLOSE 21:00 ..."   (CLOSE, not START, sometimes)
  CHARGE       -> "■VIP TICKET : ¥15,000- ... ■一般チケット : ¥5,000-"
  TICKET       -> playguide links (LivePocket etc.) + sale date
  INFO         -> organizer link (NOT a ticket link — excluded by label scoping)
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
    venue_name="横浜ベイホール",
    venue_area="Shinyamashita",
    address="3-4-17 Shinyamashita, Naka-ku, Yokohama",
    lat=None, lng=None,
)

# Event detail URLs: /schedule/{YYYY}/{MM}/{post_id}/ (the listing-month
# segment, not necessarily the event's own month — we take the date from the
# page's <h2> heading + article day number instead).
DETAIL_HREF_RE = re.compile(r"/schedule/(20\d{2})/(\d{2})/(\d+)/?$")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
# Listing/archive month heading, e.g. "07 July 2026" -> ("July", "2026").
H2_MONTH_RE = re.compile(r"([A-Za-z]+)\s+(20\d{2})")
DAY_RE = re.compile(r"\s*(\d{1,2})")
# 【公演中止】 / 【中止】 / 【公演延期】 markers appended to the h3 title.
CANCEL_MARK_RE = re.compile(r"【[^】]*(?:中止|延期)[^】]*】")

# Detail-page time roles. OPEN/START are the concert-relevant ones; CLOSE and
# other tokens are ignored (an all-day idol event may list OPEN/CLOSE only).
DETAIL_OPEN_RE = re.compile(r"OPEN[^\d]{0,8}(\d{1,2}:\d{2})", re.I)
DETAIL_START_RE = re.compile(r"START[^\d]{0,8}(\d{1,2}:\d{2})", re.I)

# Ticket playguide domains -> provider id. textutils only knows the
# t.livepocket.jp host; Bay Hall links the bare livepocket.jp/e/ form, so add
# it here (substring match also covers t.livepocket.jp).
TICKET_DOMAINS = {"livepocket.jp": "livepocket", **tu.TICKET_PROVIDERS}


class BayHallScraper(BaseScraper):
    source_id = "yokohama_bay_hall"
    source_name = "Yokohama Bay Hall"
    BASE = "https://bayhall.jp"

    def __init__(self, months_ahead: int = 8, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        # Bare /schedule/ renders the current month.
        yield from self.parse(self.fetch(f"{self.BASE}/schedule/"), month=first)
        # Forward months live at /{YYYY}/{MM}/ date archives.
        for i in range(1, self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/{m.year}/{m.month:02d}/"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break   # ran off the end of the live runway
            yield from self.parse(html, month=m)

    # --------------------------------------------------------------- listing
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        section = soup.find("section", id="leftCol") or soup

        # Month/year: prefer the page's own <h2> heading (authoritative and
        # deterministic); fall back to the pinned month= kwarg.
        year = mon = None
        h2 = section.find("h2")
        if h2:
            m = H2_MONTH_RE.search(h2.get_text(" ", strip=True))
            if m and m.group(1).lower() in MONTHS:
                mon = MONTHS[m.group(1).lower()]
                year = int(m.group(2))
        if year is None and month is not None:
            year, mon = month.year, month.month

        events: dict[str, Event] = {}
        for a in section.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            art = a.find_parent("article")
            if art is None:
                continue
            url = urljoin(self.BASE, a["href"])
            ev = self._parse_article(art, url, year, mon, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_article(self, art, url: str, year: int | None,
                       mon: int | None, today: dt.date | None) -> Event | None:
        cls = art.get("class") or []
        h3 = art.find("h3")
        raw_title = h3.get_text(" ", strip=True) if h3 else ""
        # Private/rental bookings carry no listable event.
        if "private" in cls or raw_title.upper() == "PRIVATE" or not raw_title:
            return None

        date_div = art.find("div", class_="date")
        day = None
        if date_div is not None:
            dm = DAY_RE.match(date_div.get_text())
            if dm:
                day = int(dm.group(1))
        if not day:
            return None
        date = None
        if year and mon:
            try:
                date = dt.date(year, mon, day).isoformat()
            except ValueError:
                date = None
        if date is None:            # heading missing -> weak year inference
            date = tu.infer_year(mon or dt.date.today().month, day, today)
        if not date:
            return None

        tags: list[str] = []
        if re.search(r"中止", raw_title):
            tags.append("cancelled")
        elif re.search(r"延期", raw_title):
            tags.append("postponed")
        title = CANCEL_MARK_RE.sub("", raw_title).strip() or raw_title

        # Livehouse is music-only, but stay defensive: a clearly non-concert
        # title (ice show, ceremony, expo) drops to OTHER. The site publishes
        # no category tags of its own, so this is the only signal available.
        cat = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=cat, start_date=date, tags=tags,
            **VENUE,
        )

    # ---------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill times/prices/ticket links from the event's own page. Keyed on
        the <dt> label text of the detail page's dl blocks (OPEN / START,
        CHARGE, TICKET) rather than CSS classes."""
        soup = BeautifulSoup(html, "lxml")
        article = soup.find("section", id="leftCol") or soup
        full_text = article.get_text(" ", strip=True)

        for dl in article.find_all("dl"):
            dt_el = dl.find("dt")
            dd_el = dl.find("dd")
            if dt_el is None or dd_el is None:
                continue
            label = dt_el.get_text(" ", strip=True).upper()
            dd_text = dd_el.get_text(" ", strip=True)

            if ("OPEN" in label or "START" in label) \
                    and not (ev.open_time or ev.start_time):
                o = DETAIL_OPEN_RE.search(dd_text)
                s = DETAIL_START_RE.search(dd_text)
                if o:
                    ev.open_time = o.group(1)
                if s:
                    ev.start_time = s.group(1)
            elif ("CHARGE" in label or "PRICE" in label or "料金" in label
                  or "ADV" in label) and ev.price_min is None:
                # Drop the trailing drink-charge / merch note so a ¥-marked
                # drink fee can't undercut the real ticket floor.
                zone = re.split(r"ドリンク|DRINK|物販|GOODS", dd_text)[0]
                ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(zone)
            elif "TICKET" in label and not ev.ticket_links:
                ev.ticket_links = self._ticket_links(dd_el)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(full_text):
            ev.is_sold_out = True
        return ev

    @staticmethod
    def _ticket_links(dd_el) -> list[dict]:
        links: list[dict] = []
        seen: set[str] = set()
        for a in dd_el.find_all("a", href=True):
            href = a["href"]
            provider = None
            for domain, prov in TICKET_DOMAINS.items():
                if domain in href:
                    provider = prov
                    break
            if provider and href not in seen:
                seen.add(href)
                links.append({"provider": provider, "url": href, "code": None})
        return links
