"""Scraper for Veats Shibuya — https://veats.jp

Victor Entertainment's directly-run live house in Udagawacho, Shibuya.
Standard server-rendered WordPress ("victor" theme) — no JS rendering.

Month pages: /schedule/?param=YYYYMM (e.g. ?param=202607 for July 2026).
Each month page is self-contained; the prev/this/next month nav confirms
the pattern. Walk forward, stopping after two empty months (the venue only
books ~2 months out).

Listing block = one <a class="today-lists" href="/schedule/{id}/">…</a>
carrying: day-of-month + weekday, the headline (``p.ttl`` — usually the
performer list), a secondary line (``p.text-overhidden5`` — the tour/event
name), OPEN/START times, and the LINE UP. NO price on the listing page —
that (and the ticket link) comes from the detail page, handled by
parse_detail().

Parsing keys off the detail-URL shape (/schedule/<digits>/) and the venue's
own text conventions (OPEN/START, LINE UP, ADV/DOOR, ¥) — not fragile CSS.
A structural change yields 0 events (loud), never silent garbage.
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

#: listing/detail id links — the query-string month nav (/schedule/?param=…)
#: deliberately does NOT match.
DETAIL_HREF_RE = re.compile(r"/schedule/(\d+)/?$")
#: derive the fetched month straight from the page's own "this month" nav,
#: so parse() is self-contained on a saved fixture.
THIS_MONTH_RE = re.compile(r"this-month[^>]*>\s*<a[^>]*param=(\d{4})(\d{2})", re.I)
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")


class VeatsScraper(BaseScraper):
    source_name = "Veats Shibuya"
    BASE = "https://veats.jp"

    VENUE = dict(
        venue_name="Veats Shibuya",
        venue_area="Shibuya",
        address="東京都渋谷区宇田川町33番地1号 グランド東京渋谷ビルB1・B2",
        lat=35.6614, lng=139.6981,
    )

    def __init__(self, source_id: str = "veats_shibuya",
                 months_ahead: int = 5, **kw):
        super().__init__(**kw)
        self.source_id = source_id
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/schedule/?param={m.year}{m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 2:
                break
            yield from fresh

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")

        year, mon = self._resolve_month(html, month)
        if year is None:
            # No month context and no nav to derive it from -> cannot date
            # any event. Fail loud rather than emit undated garbage.
            return []

        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not DETAIL_HREF_RE.search(a["href"]):
                continue
            url = urljoin(self.BASE, a["href"])
            ev = self._parse_block(a, url, year, mon)
            if not ev:
                continue
            key = ev.source_url
            if key in events and events[key].start_date != ev.start_date:
                # rare: same detail page listed under two dates -> keep both
                # (yokohama_arena precedent: disambiguate with a date fragment)
                ev.source_url = f"{key}#{ev.start_date}"
                key = ev.source_url
            events.setdefault(key, ev)
        return list(events.values())

    def _resolve_month(self, html: str,
                       month: dt.date | None) -> tuple[int | None, int | None]:
        if month is not None:
            return month.year, month.month
        m = THIS_MONTH_RE.search(html)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    def _parse_block(self, a, url: str, year: int, mon: int) -> Event | None:
        # --- date: day-of-month from p.day, month/year from page context ---
        day_p = a.find("p", class_="day")
        if not day_p:
            return None
        dm = re.match(r"\s*(\d{1,2})", day_p.get_text(" ", strip=True))
        if not dm:
            return None
        try:
            date = dt.date(year, mon, int(dm.group(1))).isoformat()
        except ValueError:
            return None

        # --- title (headline) + subtitle (secondary line) ---
        ttl = a.find("p", class_="ttl")
        if not ttl:
            return None                 # theme changed -> skip (loud, not garbage)
        title = _clean(ttl.get_text(" ", strip=True))
        if not title:
            return None
        sub_p = a.find("p", class_="text-overhidden5")
        subtitle = _clean(sub_p.get_text(" ", strip=True)) if sub_p else None
        if subtitle == title:
            subtitle = None

        # --- times + lineup, dispatched by the dl's <dt> label ---
        open_time = start_time = None
        lineup: list[str] = []
        for dl in a.find_all("dl"):
            dt_el, dd_el = dl.find("dt"), dl.find("dd")
            if not dt_el or not dd_el:
                continue
            label = dt_el.get_text(" ", strip=True).upper()
            dd_text = dd_el.get_text(" ", strip=True)
            if "OPEN" in label or "START" in label:
                open_time, start_time = _open_start(dd_text)
            elif "LINE" in label:       # "LINE UP"
                lineup = _split_acts(dd_text)

        category = Category.MUSIC
        if tu.is_nonmusic(f"{title} {subtitle or ''}"):
            category = Category.OTHER

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, subtitle=subtitle,
            category=category, genres=[], start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(a.get_text(" ", strip=True))),
            venue_name=self.VENUE["venue_name"],
            venue_area=self.VENUE["venue_area"],
            address=self.VENUE["address"],
            lat=self.VENUE["lat"], lng=self.VENUE["lng"],
        )

    # ---------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill price + ticket link from the event's own page. Detail markup
        is <dl><dt>LABEL</dt><dd>VALUE</dd></dl> rows; we dispatch on the dt
        label text (ADV/DOOR/料金 for price, TICKET/チケット for links) so a
        CSS refresh can't turn it into garbage. Times already come from the
        listing, so we only backfill them if absent."""
        soup = BeautifulSoup(html, "lxml")
        section = soup.find(class_="schedule-detail-area") or soup
        text = section.get_text(" ", strip=True)

        for dl in section.find_all("dl"):
            dt_el, dd_el = dl.find("dt"), dl.find("dd")
            if not dt_el or not dd_el:
                continue
            label = dt_el.get_text(" ", strip=True).upper()
            dd_text = dd_el.get_text(" ", strip=True)
            if ("OPEN" in label or "START" in label) and not (
                    ev.open_time or ev.start_time):
                ev.open_time, ev.start_time = _open_start(dd_text)
            elif ev.price_min is None and (
                    "ADV" in label or "DOOR" in label
                    or "料金" in label or "PRICE" in label):
                ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(dd_text)
            elif "TICKET" in label or "チケット" in label:
                for a in dd_el.find_all("a", href=True):
                    _add_ticket(ev.ticket_links, a["href"])

        # Known playguide domains anywhere on the page as a backstop.
        for link in tu.extract_ticket_links(soup, text):
            if link not in ev.ticket_links:
                ev.ticket_links.append(link)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev


# ------------------------------------------------------------- module helpers
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _open_start(text: str) -> tuple[str | None, str | None]:
    """First two HH:MM in the OPEN/START cell. Handles 'TBA / TBA' (-> both
    None) and two-set days ('【1部】 15:00 / 15:30 / 【2部】 18:30 / 19:00'
    -> the first set's open/start)."""
    ts = TIME_RE.findall(text)
    return (ts[0] if ts else None, ts[1] if len(ts) > 1 else None)


def _split_acts(text: str) -> list[str]:
    """Slash-separated performer list; drop trailing venue notes ('※…')."""
    acts = []
    for part in re.split(r"[/／]", text):
        name = part.split("※")[0].strip(" 　").strip()
        if name:
            acts.append(name)
    return acts


def _add_ticket(links: list[dict], href: str) -> None:
    for domain, provider in tu.TICKET_PROVIDERS.items():
        if domain in href:
            entry = {"provider": provider, "url": href, "code": None}
            break
    else:
        entry = {"provider": "other", "url": href, "code": None}
    if entry not in links:
        links.append(entry)
