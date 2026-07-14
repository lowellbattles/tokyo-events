"""Scraper for SOGO TOKYO — https://sogotokyo.com

A concert PROMOTER calendar, not a venue site: SOGO TOKYO books shows
across many halls/arenas (Kアリーナ横浜, Zepp group, NHKホール, 東京ガーデン
シアター, ...) plus a long tail of one-off halls we don't scrape directly,
some of them genuinely outside our Tokyo/Kanagawa/Chiba/Saitama scope
(河口湖ステラシアター in Yamanashi, 栃木県総合文化センター in Tochigi,
高崎 Club JAMMER'S in Gunma, ...). This is the first promoter-class source;
``venues.resolve_venue`` (built for exactly this — see its module docstring)
is the geography/curation gate: unresolvable venues are dropped, not
guessed at with local prefecture logic.

Listing: /live_information/calendar/ — one month per page, static HTML,
``?year=YYYY&month=MM`` (zero-padded); the bare URL is the current month.
prev/next links confirm the param shape. Structure is a run of
``<ul class="list--schedule"><li><dl>`` blocks, ONE PER CALENDAR DAY:
    <dt><div><p class="day">14</p><p class="week">Tue</p></div></dt>
    <dd>
      <div class="item-event">
        <a href="/live_information/detail/2577">
          <div class="block--txt box_live_2">
            <p class="artist">山本彩</p>
            <p class="title">山本彩 LIVE at 武道館</p>
            <p class="venue">日本武道館</p>
            <p class="sales ">発売中</p>       (or class="sales soldout">SOLD OUT,
          </div>                                or class="sales  not_sale"> empty)
        </a>
      </div>
      ... more <div class="item-event"> siblings for the same day ...
    </dd>
A ``<dd>`` can hold several ``item-event`` blocks (multiple shows the same
day); a multi-day run repeats the SAME detail id across consecutive day
blocks (e.g. a 2-night arena show lists identically under both dates) —
the tachikawa_stage_garden / yokohama_arena precedent applies: give each
distinct date its own Event, disambiguated with a ``#YYYY-MM-DD`` source_url
fragment only when a detail id spans more than one date.

No times or prices on the listing. Detail page (/live_information/detail/
<id>) is a clean dl/dt/dd outline keyed by JP-free ENGLISH labels — DATE,
VENUE, "OPEN / START" (dt carries class="start-time"), PRICE, TICKET,
INFORMATION — which makes it far more robust to key off than CSS classes.
Prices are written 円-suffix ("指定席8,800円（税込）", no ¥ symbol) so the
generic ¥-keyed base.parse_detail() would miss them entirely; parse_detail
is overridden to read PRICE directly (through tu.strip_drink_charges first).
INFORMATION holds the playguide anchors (w.pia.jp / l-tike.com / eplus.jp),
already known to tu.TICKET_PROVIDERS, so tu.extract_ticket_links covers them.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper

# Detail permalink: /live_information/detail/<numeric id> — the structural
# key. Anchored on the URL shape, not markup classes, so a template change
# fails loud (found=0) rather than silently parsing nothing.
DETAIL_HREF_RE = re.compile(r"/live_information/detail/\d+")

# Detail page "OPEN / START" dd body: "17:30 / 18:30" (no OPEN/START words
# next to the times — those live in the <dt> label instead, so the generic
# tu.parse_times(), which keys off the literal words, can't read this cell).
TIME_PAIR_RE = re.compile(r"(\d{1,2}:\d{2})\s*/\s*(\d{1,2}:\d{2})")
TIME_SINGLE_RE = re.compile(r"(\d{1,2}:\d{2})")

# Detail PRICE cell amounts: "8,800円" (円 suffix) or occasionally "¥8,800".
YEN_ANY_RE = re.compile(r"[¥￥]\s*([\d,]+)|([\d,]+)\s*円")


class SogoTokyoScraper(BaseScraper):
    source_id = "sogo_tokyo"
    source_name = "SOGO TOKYO"
    BASE = "https://sogotokyo.com"

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        #: raw venue strings resolve_venue() couldn't place — distinct,
        #: accumulated across scrape()/parse() calls for operator visibility
        #: (extend venues.CANONICAL/_EXTRA_ALIASES to pick these up).
        self.skipped_venues: set[str] = set()

    # ---------------------------------------------------------------- fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        base = f"{self.BASE}/live_information/calendar/"
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = base if i == 0 else f"{base}?year={m.year}&month={m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break            # far-future months eventually error out
            for ev in self.parse(html, month=m):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse: HTML string -> list[Event]. ``month`` pins the
        page's year/month (day cells carry neither) — scrape() always passes
        it; without it a date can't be resolved, so rows are dropped."""
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        rows: list[tuple[str, str, Event]] = []
        for a in soup.find_all("a", href=DETAIL_HREF_RE):
            parsed = self._parse_row(a, month)
            if parsed:
                rows.append(parsed)

        # Multi-day runs repeat the same detail id under each date it plays;
        # only fragment the URL when a run actually spans >1 distinct date
        # (tachikawa_stage_garden / yokohama_arena precedent), so single-day
        # shows keep a clean bare detail URL.
        dates_by_url: dict[str, set[str]] = defaultdict(set)
        for url, date, _ in rows:
            dates_by_url[url].add(date)

        events: dict[str, Event] = {}
        for url, date, ev in rows:
            surl = f"{url}#{date}" if len(dates_by_url[url]) > 1 else url
            ev.source_url = surl
            if surl not in events:
                events[surl] = ev
        return list(events.values())

    def _parse_row(self, a, month: dt.date | None) -> tuple[str, str, Event] | None:
        if month is None:
            return None   # no page-month context; the day cell alone can't
                           # be resolved to a calendar date
        url = urljoin(self.BASE, a["href"])

        dl = a.find_parent("dl")
        day_p = dl.find("p", class_="day") if dl else None
        if day_p is None:
            return None
        day_text = day_p.get_text(strip=True)
        if not day_text.isdigit():
            return None
        try:
            date = dt.date(month.year, month.month, int(day_text)).isoformat()
        except ValueError:
            return None

        block = a.find("div", class_="block--txt")
        if block is None:
            return None

        title_p = block.find("p", class_="title")
        title = _clean(title_p.get_text(" ", strip=True)) if title_p else ""
        if not title:
            return None

        artist_p = block.find("p", class_="artist")
        artist = _clean(artist_p.get_text(" ", strip=True)) if artist_p else ""
        lineup = [artist] if artist and artist != title else []

        venue_p = block.find("p", class_="venue")
        venue_raw = _clean(venue_p.get_text(" ", strip=True)) if venue_p else ""
        if not venue_raw:
            return None

        # Geography/curation gate: promoter calendars book halls all over
        # Kanto (and beyond); only keep venues venues.py already knows how
        # to place. Do NOT invent prefecture logic here — collect misses for
        # the operator to extend the registry with instead.
        if resolve_venue(venue_raw) is None:
            self.skipped_venues.add(venue_raw)
            return None

        sold_out = False
        sales_text = ""
        for p in block.find_all("p"):
            classes = p.get("class") or []
            if "sales" in classes:
                sales_text = _clean(p.get_text(" ", strip=True))
                sold_out = "soldout" in classes
                break
        if not sold_out:
            sold_out = bool(tu.SOLD_OUT_RE.search(sales_text))

        category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC

        ev = Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            venue_name=venue_raw, venue_area=None, address=None,
            lat=None, lng=None,
            lineup=lineup, is_sold_out=sold_out,
        )
        return (url, date, ev)

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Read the detail page's dl/dt/dd outline (DATE / VENUE / "OPEN /
        START" / PRICE / TICKET / INFORMATION). Prices are 円-suffix, which
        the generic ¥-keyed base parser can't read, so PRICE is handled here
        directly; times sit bare ("17:30 / 18:30") under a label that itself
        carries the OPEN/START words, so tu.parse_times() (which looks for
        those words next to the time) doesn't apply either.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        rows = self._outline_rows(soup)

        if not (ev.open_time or ev.start_time):
            dd = rows.get("OPEN/START")
            if dd is not None:
                when = dd.get_text(" ", strip=True)
                pair = TIME_PAIR_RE.search(when)
                if pair:
                    ev.open_time, ev.start_time = pair.group(1), pair.group(2)
                else:
                    single = TIME_SINGLE_RE.search(when)
                    if single:
                        ev.start_time = single.group(1)

        if ev.price_min is None:
            dd = rows.get("PRICE")
            if dd is not None:
                zone = tu.strip_drink_charges(dd.get_text(" ", strip=True))
                ev.price_text, ev.price_min, ev.is_free = _parse_yen_suffix(zone)

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev

    @staticmethod
    def _outline_rows(soup) -> dict[str, "BeautifulSoup"]:
        """Map the liveinfo block's <dt> label (whitespace stripped, so
        "OPEN / START" -> "OPEN/START") -> its sibling <dd> tag. Keyed off
        the site's own English field labels, not CSS, so a restyle can't
        silently swap fields."""
        out = {}
        for dl in soup.find_all("dl"):
            dt_tag = dl.find("dt")
            dd_tag = dl.find("dd")
            if not dt_tag or not dd_tag:
                continue
            label = re.sub(r"\s+", "", dt_tag.get_text(strip=True))
            out.setdefault(label, dd_tag)
        return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_yen_suffix(text: str) -> tuple[str | None, int | None, bool | None]:
    """Parse a 料金/PRICE cell written with 円-suffix and/or ¥-prefix
    amounts -> (price_text, price_min, is_free)."""
    if not text:
        return None, None, None
    yen: list[int] = []
    for a, b in YEN_ANY_RE.findall(text):
        raw = a or b
        try:
            yen.append(int(raw.replace(",", "")))
        except ValueError:
            continue
    price_text = _clean(text)[:300] or None
    if yen:
        pmin = min(yen)
        return price_text, pmin, pmin == 0
    if re.search(r"無料|入場無料", text):
        return price_text, 0, True
    return price_text, None, None
