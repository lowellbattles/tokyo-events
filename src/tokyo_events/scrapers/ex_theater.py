"""Scraper for EX THEATER ROPPONGI — https://www.ex-theater.com

A ~1,700-cap theater run by TV Asahi (Nishiazabu / Roppongi). The public
calendar page is a JS shell, but it feeds off the venue's own static JSON
endpoint (/schedule/json/schedule.json) — a single flat array covering the
whole booking history (venue opened 2013) plus many months of future dates.
Following the yokohama_arena precedent we hit that one JSON file and filter
client-side, rather than walking the ?year=&month= HTML calendar tabs.

Listing JSON record (verbatim shape):
    {"start_date":"2026-07-19", "end_date":"", "title":"... <br> ...",
     "cast":"", "show_date":"2026年7月19日(日)", "url":"schedule/2219/index.html",
     "maintenance":"", "live_event_flg":"...", "tag_names":"", ...}

Notes on the feed:
- title/cast embed <br> tags and HTML entities — strip before storing.
- maintenance == "有効" marks blank-title placeholder/hold rows -> skip.
- a "Coming Soon" title (<font ...>Coming Soon</font>) is a not-yet-announced
  slot with no facts yet -> skip until the venue fills it in.
- end_date is "" for single-day runs; multi-day runs carry a real end_date
  (occasionally equal to start_date) -> only set Event.end_date when it
  differs from start_date. Each record is ONE production (one Event), so no
  per-day splitting is needed.
- live_event_flg / tag_names are NOT reliable music indicators (a LINDBERG
  concert and a stage play share the same flags; tag_names is empty on all
  current records), so category is decided with tu.is_nonmusic on the title.

Detail pages (schedule/{id}/index.html) are static HTML with sections labelled
by Japanese header text (公演時間 / チケット料金 / チケット販売 / ドリンク).
parse_detail keys off those labels: it reads 開場/開演 for OPEN/START and pulls
price from the チケット料金 block ONLY, so the separate ￥600 drink charge is
never mistaken for the ticket price. Playguide links (Pia/Lawson/eplus) come
from the external-link anchors via the shared extractor.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

BASE = "https://www.ex-theater.com"

VENUE = dict(
    venue_name="EX THEATER ROPPONGI",
    venue_area="Roppongi",
    address="東京都港区西麻布1-2-9",
    lat=35.6613, lng=139.7255,
)

_TAG_RE = re.compile(r"<[^>]+>")
# Japanese OPEN/START anchors on the detail page ("開場 16:00 / 開演 17:00").
KAIJO_RE = re.compile(r"開場\D{0,4}(\d{1,2}:\d{2})")
KAIEN_RE = re.compile(r"開演\D{0,4}(\d{1,2}:\d{2})")


def _clean(s: str | None) -> str:
    """Strip HTML tags/entities from a feed string and collapse whitespace."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", s))).strip()


class ExTheaterScraper(BaseScraper):
    source_id = "ex_theater"
    source_name = "EX THEATER ROPPONGI"
    BASE = BASE
    SCHEDULE_URL = f"{BASE}/schedule/json/schedule.json"

    def __init__(self, months_ahead: int = 12, **kw):
        super().__init__(**kw)
        # The feed carries the venue's entire history; keep only a forward
        # window so the pipeline never stores/diffs 13 years of past shows.
        self.months_ahead = months_ahead

    # -- fetch + delegate only -------------------------------------------
    def scrape(self) -> Iterable[Event]:
        raw = self.fetch(self.SCHEDULE_URL)
        yield from self.parse(raw, today=dt.date.today().isoformat(),
                              months_ahead=self.months_ahead)

    # -- pure listing parse ----------------------------------------------
    def parse(self, raw: str, today: str | None = None,
              months_ahead: int = 12, **context) -> list[Event]:
        today = today or dt.date.today().isoformat()
        try:
            td = dt.date.fromisoformat(today)
        except ValueError:
            td = dt.date.today()
            today = td.isoformat()
        horizon = tu.add_months(td.replace(day=1), months_ahead).isoformat()

        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(rows, list):
            return []

        events: dict[str, Event] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            start_date = (row.get("start_date") or "").strip()
            if not start_date or start_date < today or start_date > horizon:
                continue
            # Placeholder/hold rows: blank title + maintenance flag.
            if row.get("maintenance") == "有効":
                continue
            title = _clean(row.get("title"))
            if not title or title.lower().startswith("coming soon"):
                continue
            url = (row.get("url") or "").strip()
            if not url:
                continue
            source_url = f"{self.BASE}/{url.lstrip('/')}"
            if source_url in events:
                continue          # dedupe by detail URL

            end_raw = (row.get("end_date") or "").strip()
            end_date = end_raw if (end_raw and end_raw != start_date) else None

            lineup = [x for x in
                      (_clean(p) for p in re.split(r"<br\s*/?>", row.get("cast") or ""))
                      if x]
            category = (Category.OTHER
                        if tu.is_nonmusic(f"{title} {' '.join(lineup)}")
                        else Category.MUSIC)

            events[source_url] = Event(
                source=self.source_id, source_url=source_url,
                title_ja=title, category=category,
                start_date=start_date, end_date=end_date,
                lineup=lineup, **VENUE,
            )
        return list(events.values())

    # -- detail enrichment (site-specific label anchors) -----------------
    def parse_detail(self, html_text: str, ev: Event) -> Event:
        soup = BeautifulSoup(html_text, "lxml")
        sections = _label_sections(soup)
        full = soup.get_text(" ", strip=True)

        # Times: prefer the 公演時間 block, fall back to whole-page text.
        # Both key off the 開場/開演 (or Latin OPEN/START) text convention.
        time_src = sections.get("公演時間") or full
        if not ev.open_time:
            ev.open_time = tu.first(KAIJO_RE, time_src) or tu.first(tu.OPEN_RE, time_src)
        if not ev.start_time:
            ev.start_time = tu.first(KAIEN_RE, time_src) or tu.first(tu.START_RE, time_src)

        # Price: ONLY the チケット料金 block, so the separately-listed ￥600
        # drink charge (ドリンク block) is never picked up as the ticket price.
        if ev.price_min is None:
            fee = sections.get("チケット料金")
            if fee:
                ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(fee)

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, full)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(full):
            ev.is_sold_out = True
        return ev


def _label_sections(soup) -> dict[str, str]:
    """Map each detail section's Japanese header text to its value text.

    Anchors on the 公演時間 / チケット料金 / ... header/value div pairs. The
    class names are a convenience; if they churn this simply returns fewer
    sections (missing fields), never garbage."""
    out: dict[str, str] = {}
    for head in soup.find_all("div", class_="show-item-table-head"):
        label = head.get_text(strip=True)
        parent = head.parent
        val = parent.find("div", class_="show-item-table-text") if parent else None
        if label and val is not None and label not in out:
            out[label] = val.get_text(" ", strip=True)
    return out
