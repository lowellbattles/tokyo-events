"""Scraper for Yokohama Arena — https://www.yokohama-arena.co.jp

The event calendar page is JS-rendered, but it feeds off the site's own
public JSON endpoint (GET /event/{YYYYMM}?_format=json, the exact call
the official page makes), which is far more robust than HTML parsing:
date1/date2, title, artist, ev_open/ev_start arrays (multi-stage shows
prefixed ①②), detail path and ticket url.

Multi-day runs share one detail path, so the per-day source_url gets a
#date fragment to keep dedupe keys unique. No detail pass needed — the
feed already carries everything the listing wants.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Iterable

from ..models import Category, Event
from .base import BaseScraper

VENUE = dict(venue_name="横浜アリーナ", venue_area="Shin-Yokohama",
             address="3-10 Shinyokohama, Kohoku-ku, Yokohama",
             lat=35.510155, lng=139.620798)

TIME_RE = re.compile(r"(\d{1,2}:\d{2})")


class YokohamaArenaScraper(BaseScraper):
    source_id = "yokohama_arena"
    source_name = "Yokohama Arena"
    BASE = "https://www.yokohama-arena.co.jp"
    supports_detail = False        # the JSON feed is already complete

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        self.session.headers["Accept"] = "application/vnd.api+json"

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            m = _add_months(first, i)
            url = f"{self.BASE}/event/{m.year}{m.month:02d}?_format=json"
            try:
                raw = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            yield from self.parse(raw)

    def parse(self, raw: str, **context) -> list[Event]:
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return []
        events: dict[str, Event] = {}
        for row in rows if isinstance(rows, list) else []:
            title = (row.get("title") or "").strip()
            date1 = row.get("date1")
            if not title or not date1:
                continue           # construction/removal-only rows
            date2 = row.get("date2") or date1
            path = row.get("path") or "/event/"
            url = f"{self.BASE}{path}#{date1}"
            artist = (row.get("artist") or "").strip()
            open_time = _first_time(row.get("ev_open"))
            start_time = _first_time(row.get("ev_start"))
            ticket = row.get("url") if isinstance(row.get("url"), str) else None
            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, category=Category.MUSIC,
                start_date=date1,
                end_date=date2 if date2 != date1 else None,
                open_time=open_time, start_time=start_time,
                lineup=[artist] if artist else [],
                ticket_url=ticket, **VENUE,
            )
            if ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())


def _first_time(vals) -> str | None:
    for v in vals if isinstance(vals, list) else []:
        m = TIME_RE.search(str(v))
        if m:
            return m.group(1)
    return None


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1)
