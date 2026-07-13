"""Scraper for 東京ガーデンシアター (Tokyo Garden Theater) — Ariake, Koto-ku.

The official venue site is operated by Sumitomo Realty & Development
(住友不動産商業マネジメント), NOT a third-party aggregator:
https://www.shopping-sumitomo-rd.com/tokyo_garden_theater/
Schedule is static, server-rendered HTML, one page per month:
  /tokyo_garden_theater/schedule/?date=YYYY-MM   (bare /schedule/ = current month)

Each event is one ``<li class="event_all event_...">`` holding:
  - ``<a href=".../schedule/{id}/">`` — stable numeric detail id (the dedupe key)
  - one or more ``<div class="ymd">`` blocks (``.m``/``.d``/``.dow``); the YEAR is
    NOT in the block — it is derived from the page's month context. Two ``.ymd``
    blocks = a multi-day run (first -> last = start/end date).
  - ``<div class="tag">`` — the venue's own category label (コンサート・ショー …)
  - ``<div class="player">`` — performer, ``<div class="title">`` — event title

TIMES / PRICES / TICKET links live only on the detail page, which uses the
Japanese conventions 【開場】(open) / 【開演】(start) and ￥N,NNN(税込) price
tiers — so this scraper ships a custom ``parse_detail`` (the generic English
OPEN/START enrichment in base.py does not fire on these labels).

Mixed calendar (~8,000-seat multi-purpose hall): besides concerts the hall
hosts non-concert bookings (e.g. a card-game world championship tagged
大会イベント). Rows whose category tag is not the venue's concert label are
kept but marked Category.OTHER (plus a tu.is_nonmusic backstop). No rows are
skipped — every ``<li>`` is a real public booking, not venue back-office.

Parser keys off the URL pattern (/schedule/{id}/) and text conventions; a
structural change collapses to found=0 (loud), never silent garbage.
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
    venue_name="東京ガーデンシアター",
    venue_area="Ariake",
    address="東京都江東区有明2-1-6",
    lat=35.6347,
    lng=139.7925,
)

# The venue's own category label for concerts/shows. Anything else the site
# tags a row as (大会イベント, 展示会, …) is treated as non-concert -> OTHER.
# This is the site's tag, deliberately NOT a broad hand-rolled keyword list.
MUSIC_TAGS = {"コンサート・ショー"}

DETAIL_HREF_RE = re.compile(r"/tokyo_garden_theater/schedule/(\d+)/")
NAV_DATE_RE = re.compile(r"[?&]date=(\d{4})-(\d{2})")
KAIJO_RE = re.compile(r"開場[】\s　:：]*(\d{1,2}:\d{2})")   # 【開場】17:30
KAIEN_RE = re.compile(r"開演[】\s　:：]*(\d{1,2}:\d{2})")   # 【開演】18:30
_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class GardenTheaterScraper(BaseScraper):
    source_id = "tokyo_garden_theater"
    source_name = "Tokyo Garden Theater"
    BASE = "https://www.shopping-sumitomo-rd.com"
    SCHEDULE = "/tokyo_garden_theater/schedule/"

    def __init__(self, months_ahead: int = 8, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        """Walk month pages forward from the current month. The bare
        /schedule/ URL is the current month; later months use ?date=YYYY-MM.
        Stop after two consecutive empty months (a single gap is tolerated),
        deduping the cross-month event that a multi-day run appears under in
        both its start and end month."""
        base = self.BASE + self.SCHEDULE
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = base if i == 0 else f"{base}?date={m.year}-{m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 3:
                break
            yield from fresh

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse. ``month`` pins the page's year-month for
        deterministic tests; when omitted it is recovered from the page's
        own active month-nav / footer, falling back to today-relative year
        inference per date."""
        soup = BeautifulSoup(html, "lxml")
        anchor = self._anchor(soup, month)
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            li = a.find_parent("li")
            if li is None:
                continue
            url = urljoin(self.BASE, a["href"])
            ev = self._parse_block(li, url, anchor, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, li, url: str, anchor: dt.date | None,
                     today: dt.date | None) -> Event | None:
        dates = self._dates(li, anchor, today)
        if not dates:
            return None            # no parseable date -> drop (loud per row)
        start_date = dates[0]
        end_date = dates[-1] if dates[-1] != start_date else None

        title = self._clean(li.find("div", class_="title"))
        player = self._clean(li.find("div", class_="player"))
        if not title:
            title = player
        if not title:
            return None

        tag_el = li.find("div", class_="tag")
        tag = tag_el.get_text(" ", strip=True) if tag_el else ""
        li_classes = li.get("class", [])

        is_concert = (tag in MUSIC_TAGS) or ("event_concert" in li_classes)
        category = Category.MUSIC if is_concert else Category.OTHER
        if tu.is_nonmusic(f"{title} {player or ''}"):
            category = Category.OTHER    # ice show / championship mis-tagged

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category,
            start_date=start_date, end_date=end_date,
            lineup=[player] if player and player != title else [],
            tags=[tag] if tag else [],
            **VENUE,
        )

    # --------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill times / prices / ticket links from the event's own page.
        The venue writes times as 【開場】HH:MM / 【開演】HH:MM and prices as
        ・<席種> ￥N,NNN(税込) in the INFORMATION block — neither of which the
        generic base.parse_detail (English OPEN/START, ADV/前売 zones) catches.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        if not (ev.open_time or ev.start_time):
            zone = text
            for dl in soup.find_all("dl"):
                d = dl.find("dt")
                if d and "OPEN" in d.get_text(" ", strip=True).upper():
                    dd = dl.find("dd")
                    if dd:
                        zone = dd.get_text(" ", strip=True)
                    break
            mo = KAIJO_RE.search(zone) or KAIJO_RE.search(text)
            ma = KAIEN_RE.search(zone) or KAIEN_RE.search(text)
            ev.open_time = mo.group(1) if mo else None
            ev.start_time = ma.group(1) if ma else None
            if not (ev.open_time or ev.start_time):
                ev.open_time, ev.start_time = tu.parse_times(text)

        if ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(
                self._price_zone(soup))

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _clean(el) -> str | None:
        """Text of a listing div with <br> turned into spaces and runs of
        whitespace collapsed."""
        if el is None:
            return None
        for br in el.find_all("br"):
            br.replace_with(" ")
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        return txt or None

    def _dates(self, li, anchor: dt.date | None,
               today: dt.date | None) -> list[str]:
        out: list[str] = []
        for ymd in li.select("div.ymd"):
            mm = dd = None
            m_el, d_el = ymd.find("div", class_="m"), ymd.find("div", class_="d")
            if (m_el and d_el and m_el.get_text(strip=True).isdigit()
                    and d_el.get_text(strip=True).isdigit()):
                mm, dd = int(m_el.get_text(strip=True)), int(d_el.get_text(strip=True))
            else:                       # text fallback if .m/.d classes churn
                mt = re.search(r"(\d{1,2})\D+(\d{1,2})",
                               ymd.get_text(" ", strip=True))
                if mt:
                    mm, dd = int(mt.group(1)), int(mt.group(2))
            if mm is None:
                continue
            iso = self._iso(mm, dd, anchor, today)
            if iso:
                out.append(iso)
        return out

    @staticmethod
    def _iso(month: int, day: int, anchor: dt.date | None,
             today: dt.date | None) -> str | None:
        """Attach a year to a bare month/day. With a page anchor, pick the
        candidate year whose date sits closest to the anchor month — this
        handles multi-day runs that spill into the next month and Dec->Jan
        rollovers. Without an anchor, fall back to today-relative inference."""
        if anchor is not None:
            best = None
            for y in (anchor.year - 1, anchor.year, anchor.year + 1):
                try:
                    cand = dt.date(y, month, day)
                except ValueError:
                    continue
                diff = abs((cand - anchor).days)
                if best is None or diff < best[0]:
                    best = (diff, cand)
            return best[1].isoformat() if best else None
        return tu.infer_year(month, day, today)

    def _anchor(self, soup, month: dt.date | None) -> dt.date | None:
        if month is not None:
            return dt.date(month.year, month.month, 1)
        active = soup.select_one("li.-active a[href*='date=']")
        if active:
            m = NAV_DATE_RE.search(active.get("href", ""))
            if m:
                return dt.date(int(m.group(1)), int(m.group(2)), 1)
        foot = soup.find("div", class_="scheduleFooter")
        if foot:
            spans = [s.get_text(strip=True) for s in foot.find_all("span")]
            yr = next((int(s) for s in spans if re.fullmatch(r"20\d{2}", s)), None)
            mon = None
            for s in spans:
                mon = _MONTH_ABBR.get(s.strip(". ").lower()[:3])
                if mon:
                    break
            if yr and mon:
                return dt.date(yr, mon, 1)
        return None

    @staticmethod
    def _price_zone(soup) -> str:
        """Collect only the ￥-bearing tier lines from the INFORMATION block
        (between <h3 class="information"> and the contact/ticket sections),
        keeping facts (seat type + price) and dropping the prose notes."""
        h3 = soup.find("h3", class_="information")
        if not h3:
            return ""
        stop = {"eventContact", "ticketBtns", "backBtns"}
        lines: list[str] = []
        for sib in h3.next_siblings:
            name = getattr(sib, "name", None)
            if name == "div" and stop.intersection(sib.get("class", [])):
                break
            txt = str(sib) if name is None else sib.get_text(" ", strip=True)
            txt = re.sub(r"\s+", " ", txt).strip()
            if "¥" in txt or "￥" in txt:
                lines.append(txt)
        return " / ".join(lines)
