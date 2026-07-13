"""Scraper for 東京オペラシティ コンサートホール / リサイタルホール
(Tokyo Opera City Concert Hall + Recital Hall) — Hatsudai, Nishi-Shinjuku.

Official calendar: https://www.operacity.jp/concert/ . The visible
/concert/calendar/ page (and each /concert/calendar/detail.php?id=N page)
is a thin jQuery shell that AJAX-loads an HTML fragment from the site's own
internal endpoint. We fetch that endpoint directly — it is clean, stable,
server-rendered, and needs no headless browser:

    listing (per month):
      /contents/performance?lang=ja&year={YYYY}&month={M}
          &presented_only=0&calendar=0&past=0

Month pagination is just the year/month query params; the site publishes a
rolling ~9-month window (a <select id="month"> in the fragment lists it).
We walk months forward and stop after two empty months.

One month fragment is a list of <li class="c-calendar__list ..."> cards::

    <div class="c-calendar__lists__box__date"> 7/2<span>［木］</span> </div>
    <div class="c-calendar__list__label__time"> 19:00 </div>
    <div class="c-calendar__list__label__place _concert"> コンサートホール </div>
    <div class="c-calendar__list__texts__title">
      <a href="/concert/calendar/detail.php?id=17717">…title…</a></div>
    <dd class="c-calendar__list__texts__dd">
      <a href="https://promoter.example/…">promoter</a> …</dd>

Conventions this parser relies on (text/URL, not fragile CSS):
- Real performances are exactly the cards carrying a
  /concert/calendar/detail.php?id=N link. Non-event rows the hall lists for
  its own operations — 保守点検 (maintenance), リハーサル (rehearsal),
  公演予定 / 公演予定（関係者のみ） (private/planned bookings) — have NO such
  link and are skipped entirely (venue's own business, not public events).
- One time only (開演/start); this hall does NOT use OPEN/START door-vs-start
  pairs like live houses do. A couple of cards list two showtimes
  (matinee+evening under one id) — we keep the first (earliest).
- Hall room comes from the literal text コンサートホール vs リサイタルホール.
- The 「お問い合わせ」 <dd> link is the official promoter/ticket contact and
  can point at any external domain (kajimotomusic.com, cityphil.jp, an
  orchestra's own site, …); it is kept as the ticket link. Some cards have a
  text-only contact (no URL) — then no ticket link is invented.

supports_detail is False: the listing already carries every fact we store
(title, date, single start time, room, source URL, promoter link). The
richer /contents/performance/{id} fragment (price tiers, 出演 lineup, 曲目)
exists, but the pipeline's detail pass fetches ev.source_url — which for
this venue is the JS shell, not the fragment — and would re-fetch every
price-less concert (many here are contact/phone-only) forever. Wiring that
up is a future enhancement (see the module notes / caveats).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper

VENUE_AREA = "Hatsudai"
# Tokyo Opera City is a major, fixed Tokyo landmark (Nishi-Shinjuku 3-20-2);
# these coords are well established. Address left to the integrator (the probe
# did not fetch the access page, so we do not assert a street address).
LAT, LNG = 35.6837, 139.6864
HALL_CONCERT = "東京オペラシティ コンサートホール"
HALL_RECITAL = "東京オペラシティ リサイタルホール"
HALL_GENERIC = "東京オペラシティ"

# Operacity's OWN event links only. Note a promoter contact link may itself be
# a "detail.php?id=" URL (e.g. cityphil.jp/concert/detail.php?id=740) — the
# "/concert/calendar/" segment is what pins it to this venue's own calendar.
DETAIL_HREF_RE = re.compile(r"/concert/calendar/detail\.php\?id=(\d+)")
MD_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
YEAR_RE = re.compile(r"(20\d{2})")

# JP registry second-level labels to skip when deriving a provider slug.
_HOST_SKIP = {"www", "info", "ticket", "tickets"}
_TLD_SKIP = {"co", "or", "ne", "ac", "go", "ad", "ed", "gr", "lg",
             "com", "net", "org", "jp", "io"}


class OperaCityScraper(BaseScraper):
    source_id = "opera_city"
    source_name = "東京オペラシティ コンサートホール"
    BASE = "https://www.operacity.jp"
    supports_detail = False        # every stored fact is on the listing

    def __init__(self, months_ahead: int = 9, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ----------------------------------------------------------------- fetch
    def _month_url(self, year: int, month: int) -> str:
        return (f"{self.BASE}/contents/performance?lang=ja&year={year}"
                f"&month={month}&presented_only=0&calendar=0&past=0")

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            try:
                html = self.fetch(self._month_url(m.year, m.month))
            except RuntimeError:
                if i == 0:
                    raise                 # month 0 must work; else fail loud
                break                     # far-future months may 404 — stop
            evs = self.parse(html, month=m)
            empty_streak = 0 if evs else empty_streak + 1
            yield from evs
            if empty_streak >= 2:
                break

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        year = month.year if month else self._headline_year(soup)
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            url = urljoin(self.BASE, a["href"])    # keep ?id= query
            block = a.find_parent("li") or a
            ev = self._parse_block(a, block, url, year, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    @staticmethod
    def _headline_year(soup) -> int | None:
        h = soup.find(id="headline")
        if h is not None:
            m = YEAR_RE.search(h.get_text(" ", strip=True))
            if m:
                return int(m.group(1))
        return None

    def _parse_block(self, a, block, url: str, year: int | None,
                     month: dt.date | None, today: dt.date | None
                     ) -> Event | None:
        block_text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))

        # --- date: "M/D" from the date cell (fallback: first M/D in block) ---
        date_div = block.find(class_="c-calendar__lists__box__date")
        date_src = date_div.get_text(" ", strip=True) if date_div else block_text
        md = MD_RE.search(date_src)
        if not md:
            return None
        mo, day = int(md.group(1)), int(md.group(2))
        y = year if year is not None else (month.year if month else None)
        if y is not None:
            try:
                date = dt.date(y, mo, day).isoformat()
            except ValueError:
                return None
        else:
            date = tu.infer_year(mo, day, today)
        if not date:
            return None

        # --- title (from the detail anchor; <br> -> space) ---
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if not title:
            return None

        # --- start time (single; keep the first if a card lists two) ---
        time_div = block.find(class_="c-calendar__list__label__time")
        time_src = (time_div.get_text(" ", strip=True) if time_div is not None
                    else block_text.split(title, 1)[0])   # churn fallback
        tm = TIME_RE.search(time_src)
        start_time = tm.group(1) if tm else None

        # --- room (literal text convention) ---
        place = block.find(class_="c-calendar__list__label__place")
        place_src = place.get_text(" ", strip=True) if place is not None \
            else block_text
        if "リサイタルホール" in place_src:
            venue_name = HALL_RECITAL
        elif "コンサートホール" in place_src:
            venue_name = HALL_CONCERT
        else:
            venue_name = HALL_GENERIC

        # --- promoter / ticket link (official 「お問い合わせ」 outbound link) ---
        ticket_url, ticket_links = self._ticket(block)

        category = (Category.OTHER if tu.is_nonmusic(title)
                    else Category.MUSIC)
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, genres=[],
            start_date=date, start_time=start_time,
            venue_name=venue_name, venue_area=VENUE_AREA,
            address=None, lat=LAT, lng=LNG,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(block_text)),
            ticket_url=ticket_url, ticket_links=ticket_links,
        )

    @staticmethod
    def _ticket(block) -> tuple[str | None, list[dict]]:
        for a2 in block.find_all("a", href=True):
            href = a2["href"].strip()
            if DETAIL_HREF_RE.search(href):
                continue                       # this venue's own detail link
            if href.startswith(("http://", "https://")) and "@" not in href:
                return href, [{"provider": _provider_for(href),
                               "url": href, "code": None}]
        return None, []


def _provider_for(url: str) -> str:
    """Best-effort provider slug for a ticket/promoter URL. Known playguides
    map to their canonical id; otherwise use the registrable hostname label
    (https://www.japanarts.co.jp/... -> 'japanarts')."""
    for domain, provider in tu.TICKET_PROVIDERS.items():
        if domain in url:
            return provider
    m = re.search(r"https?://([^/]+)", url)
    host = (m.group(1) if m else url).split(":")[0]
    parts = [p for p in host.split(".") if p not in _HOST_SKIP]
    sig = [p for p in parts if p not in _TLD_SKIP]
    if sig:
        return sig[-1]
    return parts[0] if parts else "vendor"
