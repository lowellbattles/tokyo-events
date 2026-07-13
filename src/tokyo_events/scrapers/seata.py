"""Scraper for 吉祥寺CLUB SEATA — https://www.seata.jp

Kichijoji live house / club, part of the ベースオントップ (Base On Top) group
(shared CMS with 新宿Zirco Tokyo, 大塚Deepa, several Osaka venues). Fully
static HTML, no JS render, no robots.txt (404).

Month pages: /schedule/calendar/{YYYY}/{MM}/ (bare /schedule/calendar/ is the
current month). The in-page nav exposes ~12 forward months. Detail pages live
at /schedule/detail/{numeric_id}; the id is an opaque CMS auto-increment, so it
must be read off the calendar page (never guessed from the date).

Listing shape — the parser keys off the calendar grid, which carries a clean,
unambiguous ``data-date="YYYYMMDD"`` on every cell::

    <td data-date="20260701">
      <div class="day"><div class="day_num">1</div>
        <a href="https://www.seata.jp/schedule/detail/46860"
           class="schedule schedule_category_5 subject unfinished">Minillon vol.5</a>
      </div>
    </td>

Date + title + detail URL come from that grid. A cell may hold several <a>
(distinct events sharing one day) — each has its own detail id, so dedupe by
source_url keeps them all.

GOTCHA: below the grid a second "scheduleList" section repeats every event with
OPEN/START and price values — but those are wrapped in an HTML comment and are
fake boilerplate ("OPEN:00:00 / START:23:59" for every event). BeautifulSoup
drops comment nodes from get_text(), so they never leak; real times/prices come
only from the per-event detail page (parse_detail below).

Detail page (uncommented, populated)::

    <div class="scheduleCnt">
      <h1>Minillon vol.5</h1>
      <dl class="openTime"><dt>[OPEN/START]</dt><dd>OPEN15:10/START15:30</dd></dl>
      <dl class="price"><dt>[料金]</dt>
          <dd>ADV/DOOR ￥3,500/￥4,500 (1Drink代金￥700別途必要)</dd></dl>
      <ul class="ticketBnr"></ul>
    </div>

The price line always trails a "(...1Drink代金￥700別途必要)" surcharge note; the
￥700 drink fee is NOT the ticket price, so parse_detail strips that
parenthetical before taking the cheapest tier. Prices also appear as bare
"3500円" on some events, so the amount parser accepts ￥ / 円 / yen forms.
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

# Detail links: /schedule/detail/<numeric id> (opaque CMS auto-increment).
DETAIL_HREF_RE = re.compile(r"/schedule/detail/\d+")
DATA_DATE_RE = re.compile(r"^\d{8}$")

# Price amounts on the detail page: ¥N,NNN | N,NNN円/yen | comma-grouped bare.
# Ordered branches; the bare branch (no ¥/円) is intentionally omitted here —
# every SEATA price carries an explicit ¥ or 円, so we avoid matching stray
# digits (e.g. "vol.5", "1Drink").
PRICE_NUM_RE = re.compile(
    r"[¥￥]\s*(\d[\d,，]*)"
    r"|(\d[\d,，]*)\s*(?:yen|円)",
    re.I,
)
# The trailing "(...1Drink代金￥700別途必要)" surcharge note — stripped so its
# ￥700 drink fee is never mistaken for the cheapest ticket tier.
DRINK_PAREN_RE = re.compile(
    r"[(（][^)）]*(?:Drink|ドリンク|ワンドリンク|1D\b|1D代|D代|飲食|込み|別途)"
    r"[^)）]*[)）]",
    re.I,
)
FREE_RE = re.compile(r"入場無料|無料|FREE", re.I)


def _amounts(text: str) -> list[int]:
    out: list[int] = []
    for m in PRICE_NUM_RE.finditer(text):
        g = m.group(1) or m.group(2)
        if g:
            try:
                out.append(int(g.replace(",", "").replace("，", "")))
            except ValueError:
                pass
    return out


def _parse_price(raw: str) -> tuple[str | None, int | None, bool | None]:
    """(price_text, price_min, is_free) from a SEATA ``dl.price dd`` string.

    Drops the drink-surcharge parenthetical before taking the cheapest tier so
    the ￥700 (etc.) 1-drink fee never wins.
    """
    if not raw:
        return None, None, None
    amounts = _amounts(DRINK_PAREN_RE.sub(" ", raw))
    if amounts:
        pmin = min(amounts)
        return raw[:300], pmin, pmin == 0
    if FREE_RE.search(raw):
        return raw[:300], 0, True
    return raw[:300], None, None


# Group family lives on one CMS template; only club_seata is in geographic
# scope today. Adding Zirco Tokyo / Deepa later is just another dict entry.
VENUES = {
    "club_seata": dict(
        base="https://www.seata.jp",
        venue_name="吉祥寺CLUB SEATA",
        venue_area="Kichijoji",
        address="〒180-0004 東京都武蔵野市吉祥寺本町1-20-3 吉祥寺パーキングプラザB1F",
        lat=35.70575, lng=139.57962),
}


class SeataScraper(BaseScraper):
    source_name = "吉祥寺CLUB SEATA"

    def __init__(self, venue_id: str = "club_seata", months_ahead: int = 4,
                 **kw):
        super().__init__(**kw)
        if venue_id not in VENUES:
            raise ValueError(f"unknown SEATA-family venue: {venue_id}")
        self.venue = VENUES[venue_id]
        self.source_id = venue_id
        self.BASE = self.venue["base"]
        self.months_ahead = months_ahead

    # --- fetching ---------------------------------------------------------
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/schedule/calendar/{m.year}/{m.month:02d}/"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            # Dense venue: empty months only appear past the schedule horizon.
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 3:
                break
            yield from fresh

    # --- pure parse -------------------------------------------------------
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Parse one calendar month page. ``month``/``today`` are accepted for
        signature parity but unused: every cell carries an absolute
        ``data-date``, so dating is deterministic without them."""
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for td in soup.find_all("td", attrs={"data-date": True}):
            raw = (td.get("data-date") or "").strip()
            if not DATA_DATE_RE.match(raw):
                continue
            try:
                date = dt.date(int(raw[0:4]), int(raw[4:6]),
                               int(raw[6:8])).isoformat()
            except ValueError:
                continue
            for a in td.find_all("a", href=True):
                if not DETAIL_HREF_RE.search(a["href"]):
                    continue
                url = urljoin(self.BASE, a["href"])
                if url in events:
                    continue
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
                if not title:
                    continue
                ev = self._build(url, title, date)
                events[url] = ev
        return list(events.values())

    def _build(self, url: str, title: str, date: str) -> Event:
        # Pure live house -> everything is a concert; the is_nonmusic guard is
        # a cheap safety net that fires only on an obviously non-musical title.
        category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, genres=[],
            start_date=date,
            venue_name=self.venue["venue_name"],
            venue_area=self.venue["venue_area"],
            address=self.venue["address"],
            lat=self.venue["lat"], lng=self.venue["lng"],
        )

    # --- detail enrichment ------------------------------------------------
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill OPEN/START, cheapest price tier, ticket links and sold-out from
        the event's own detail page. Overrides the generic base version because
        SEATA's price line always trails a drink-fee parenthetical that the
        generic min-of-all-¥ logic would wrongly pick up."""
        soup = BeautifulSoup(html, "lxml")
        cnt = soup.select_one("div.scheduleCnt") or soup

        if not (ev.open_time or ev.start_time):
            dd = cnt.select_one("dl.openTime dd")
            if dd:
                ev.open_time, ev.start_time = tu.parse_times(
                    dd.get_text(" ", strip=True))

        if ev.price_min is None:
            dd = cnt.select_one("dl.price dd")
            if dd:
                raw = re.sub(r"\s+", " ", dd.get_text(" ", strip=True)).strip()
                ev.price_text, ev.price_min, ev.is_free = _parse_price(raw)

        if not ev.ticket_links:
            bnr = cnt.select_one("ul.ticketBnr") or cnt
            ev.ticket_links = tu.extract_ticket_links(
                bnr, bnr.get_text(" ", strip=True))

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(
                cnt.get_text(" ", strip=True)):
            ev.is_sold_out = True
        return ev
