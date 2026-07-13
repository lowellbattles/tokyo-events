"""Scraper for SGC HALL ARIAKE (SGCホール有明) — https://tdp.tv-asahi.co.jp/hall/

A brand-new (opened 2026-03-27) ~3,767-seat music hall run by TV Asahi as
part of "Tokyo Dream Park" in Ariake, Koto-ku. Same company / same shape as
EX THEATER ROPPONGI (see ex_theater.py): the /hall/ calendar page is a jQuery
shell, but the event data is served from the venue's own static JSON feed
(/hall/json/hall-event.json — one flat array of every performance, past and
future) and each detail page (/hall/event/{id}/) is static HTML. Following the
ex_theater / yokohama_arena precedent we hit that one JSON file and filter
client-side rather than driving a headless browser.

Listing feed record (verbatim shape):
    {"id":"0057", "startDate":"2026-12-05", "endDate":"2026-12-06",
     "category":"sgchall", "title":"斉藤和義<br>KAZUYOSHI SAITO LIVE TOUR 2026",
     "thumbnail":"/hall/event/0057/images/...jpg", "link":"/hall/event/0057/",
     "blank":false}

Notes on the feed:
- startDate/endDate are ISO YYYY-MM-DD; endDate is "" for single-day runs and a
  real date for multi-day tours (each record is ONE production -> one Event, so
  no per-day splitting; end_date is only set when it differs from startDate).
- title is an HTML string: "<br>" separates the act/series from the tour /
  sub-title. We split on <br>: first line -> title_ja, the remainder -> subtitle.
  Titles are kept in their official styling (tags stripped, entities decoded,
  whitespace collapsed) rather than NFKC-folded — normalization belongs to the
  later artist-crossref phase.
- Reserved-but-unannounced slots carry a placeholder title
  ("<span class='today-none'></span>") with an empty thumbnail -> after tag
  stripping the title is empty, so they are skipped.
- category is always the literal "sgchall" (a venue tag, NOT an event-type
  label), so there is no per-event type hint in the feed; concert-vs-not is
  decided with the shared tu.is_nonmusic() against the title text.
- The feed is NOT pre-filtered to upcoming — it carries the venue's whole
  history back to its 2026-03-28 opening. We keep only rows whose run has not
  finished (endDate-or-startDate >= today) and that start within a forward
  window, so the pipeline never stores/diffs the back-catalogue.

Detail pages (/hall/event/{id}/) are static HTML: an "開催概要" block of
<table class="article-table"> rows, each <th> a Japanese label + <td> the
value. Labels: 公演日 (dates), 公演時間 (開場=OPEN / 開演=START, often per-day),
チケット料金 (¥ tiers, e.g. 指定席 ￥8,800(税込)), チケット販売 (on-sale date),
問い合わせ (promoter/contact), 備考 (notes). parse_detail keys off the <th>
label text (not CSS classes) and pulls OPEN/START from 公演時間 and the ¥ price
from チケット料金 ONLY, so notes/on-sale dates never masquerade as the price.
Playguide links (Pia/Lawson/eplus/...) come from the shared extractor.
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

BASE = "https://tdp.tv-asahi.co.jp"

VENUE = dict(
    venue_name="SGCホール有明",
    venue_area="Ariake",
    address="東京都江東区有明1丁目3番33号",
    lat=35.636, lng=139.793,          # approximate (Ariake 1-chome); see caveats
)

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
# Japanese OPEN/START anchors on the detail page ("開場 16:30 / 開演 17:30").
KAIJO_RE = re.compile(r"開場\D{0,4}(\d{1,2}:\d{2})")
KAIEN_RE = re.compile(r"開演\D{0,4}(\d{1,2}:\d{2})")


def _clean(s: str | None) -> str:
    """Strip HTML tags/entities from a feed string, collapse whitespace."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", s))).strip()


def _split_title(raw: str | None) -> tuple[str, str | None, str]:
    """('act<br>tour') -> (title_ja, subtitle_or_None, full_title).

    First <br>-delimited line is the act/series, the rest is the tour /
    sub-title. full_title is the whole thing (used for category detection)."""
    parts = [p for p in (_clean(x) for x in _BR_RE.split(raw or "")) if p]
    if not parts:
        return "", None, ""
    title = parts[0]
    subtitle = " ".join(parts[1:]) or None
    return title, subtitle, " ".join(parts)


class SgcHallScraper(BaseScraper):
    source_id = "sgc_hall_ariake"
    source_name = "SGC HALL ARIAKE"
    BASE = BASE
    SCHEDULE_URL = f"{BASE}/hall/json/hall-event.json"

    def __init__(self, months_ahead: int = 12, **kw):
        super().__init__(**kw)
        # The single feed carries the venue's whole history; keep only a
        # forward window so the pipeline never diffs the back-catalogue.
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
            start_date = (row.get("startDate") or "").strip()
            if not start_date:
                continue
            end_raw = (row.get("endDate") or "").strip()
            eff_end = end_raw or start_date
            if eff_end < today:            # run already finished
                continue
            if start_date > horizon:       # too far in the future
                continue

            title_ja, subtitle, full_title = _split_title(row.get("title"))
            if not title_ja:               # placeholder / unannounced slot
                continue

            link = (row.get("link") or "").strip()
            if not link:
                _id = (row.get("id") or "").strip()
                if not _id:
                    continue
                link = f"/hall/event/{_id}/"
            source_url = f"{self.BASE}/{link.lstrip('/')}"
            if source_url in events:       # dedupe by detail URL
                continue

            end_date = end_raw if (end_raw and end_raw != start_date) else None
            category = (Category.OTHER if tu.is_nonmusic(full_title)
                        else Category.MUSIC)

            events[source_url] = Event(
                source=self.source_id, source_url=source_url,
                title_ja=title_ja, subtitle=subtitle, category=category,
                start_date=start_date, end_date=end_date, **VENUE,
            )
        return list(events.values())

    # -- detail enrichment (site-specific label anchors) -----------------
    def parse_detail(self, html_text: str, ev: Event) -> Event:
        soup = BeautifulSoup(html_text, "lxml")
        sections = _label_sections(soup)
        full = soup.get_text(" ", strip=True)

        # Times: prefer the 公演時間 row, fall back to whole-page text. Both key
        # off the 開場/開演 (or Latin OPEN/START) text convention. Multi-day runs
        # list per-day times; we keep the first day's OPEN/START.
        time_src = sections.get("公演時間") or full
        if not ev.open_time:
            ev.open_time = tu.first(KAIJO_RE, time_src) or tu.first(tu.OPEN_RE, time_src)
        if not ev.start_time:
            ev.start_time = tu.first(KAIEN_RE, time_src) or tu.first(tu.START_RE, time_src)

        # Price: ONLY the チケット料金 row, so on-sale dates / age notes / the
        # 備考 block never get mistaken for the ticket price.
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
    """Map each detail table row's Japanese <th> label to its <td> value text.

    Keys off the table's th=label / td=value structure (a semantic convention),
    not CSS class names; if the markup churns this simply returns fewer sections
    (missing fields), never garbage."""
    out: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None or td is None:
            continue
        label = re.sub(r"\s+", "", th.get_text())
        if label and label not in out:
            out[label] = td.get_text(" ", strip=True)
    return out
