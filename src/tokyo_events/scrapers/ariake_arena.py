"""Scraper for 有明アリーナ / TOKYO ARIAKE ARENA — https://ariake-arena.tokyo

Static, server-rendered WordPress site. The EVENT calendar is paginated by
FIVE fixed relative-offset slugs (NOT a ?ym=/YYYYMM parameter):

    /event/        current month
    /event/next/   +1 month
    /event/two/    +2
    /event/three/  +3
    /event/last/   +4

Together they give a rolling ~5-month window; there is no "all upcoming"
view and no way to reach further out, so scrape() just hits the fixed slugs
each run (per the probe's recommendation) rather than walking calendar months.

There is NO per-event detail page on the venue's own domain — each listing
card is fully self-contained (title, dates, OPEN/START times, price, an
external official/ticket link). So supports_detail = False and the listing
IS the record. Each card is one <li id="detail-NNN"> (a stable WordPress post
id) inside <ul class="event_detail_list">; source_url uses the venue's own
anchor: https://ariake-arena.tokyo/event/#detail-NNN — stable across which
month tab the card currently sits under.

Card conventions the parser keys off (TEXT, not CSS decoration):
- .event_day span text is "M.D DOW" (e.g. "7.4 SAT"); one span per listed
  day. A trailing "-" on the first span ("7.17 FRI -") marks a date RANGE to
  the next listed day; we record start = first date, end = last date either
  way. The bare M.D carries no year — the year comes from the page's own tab
  menu (month_number -> year), with tu.infer_year as a fallback.
- .event_name = short/artist title (the reliable field); .sub_title = full
  tour/show title (sometimes empty) -> stored as subtitle.
- Times in the 公演時間 row use 開場 (OPEN) / 開演 (START); multi-show days
  list several — we take the earliest (first in document order). Sports rows
  use TIPOFF / 試合 instead and carry no 開演.
- Prices in the 料金 row use "10,500円" and "¥10,000" interchangeably, one
  tier per line; we take the min of each line's leading (headline) amount so
  a "+アップグレード ¥5,500" component line can't undercut the real floor.

MIXED CALENDAR: this arena interleaves concerts with sports fixtures
(basketball national-team games, volleyball, handball) and ice shows. Those
non-concert rows are kept (facts-only) but tagged Category.OTHER via
tu.is_nonmusic plus the venue's own non-concert vocabulary seen on the
schedule (TIPOFF / 試合 / スポーツフェス). Everything else is Category.MUSIC.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(
    venue_name="有明アリーナ（TOKYO ARIAKE ARENA）",
    venue_area="Ariake",
    # address / lat / lng deliberately omitted: the probe could not confirm
    # them from the fetched pages (they live on a separate /access/ page).
)

# Fixed relative-offset month slugs (current month first).
MONTH_SLUGS = ("", "next/", "two/", "three/", "last/")

# "7.4 SAT" / "7.17 FRI -"  -> month, day  (trailing DOW/"-" ignored)
DAY_RE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})")
KAIJO_RE = re.compile(r"開場\s*(\d{1,2}:\d{2})")          # OPEN
KAIEN_RE = re.compile(r"開演\s*(\d{1,2}:\d{2})")          # START
TIPOFF_RE = re.compile(r"TIP[\s-]?OFF\s*(\d{1,2}:\d{2})", re.I)
# ¥12,100 / ￥12,100 / 10,500円
AMOUNT_RE = re.compile(r"[¥￥]\s*([\d,，]+)|([\d,，]+)\s*円")
FREE_RE = re.compile(r"無料")
# The venue's OWN non-concert vocabulary as it appears on the schedule. This
# is not an invented broad keyword list — TIPOFF is basketball tip-off time,
# 試合 is a sports "match/game", スポーツフェス is the venue's sports festival.
VENUE_OTHER_RE = re.compile(r"TIP[\s-]?OFF|試合|スポーツフェス", re.I)


class AriakeArenaScraper(BaseScraper):
    source_id = "ariake_arena"
    source_name = "TOKYO ARIAKE ARENA"
    BASE = "https://ariake-arena.tokyo"
    supports_detail = False        # listing cards are already complete

    def __init__(self, max_months: int = 5, **kw):
        super().__init__(**kw)
        # How many of the fixed slugs to fetch (1..5). Default = full window.
        self.max_months = max(1, min(max_months, len(MONTH_SLUGS)))

    def scrape(self) -> Iterable[Event]:
        seen: set[str] = set()
        for i, slug in enumerate(MONTH_SLUGS[: self.max_months]):
            url = f"{self.BASE}/event/{slug}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                continue    # a future-month slug failing must not drop earlier data
            for ev in self.parse(html):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # -- pure parse (html in, Events out); today= only feeds the year fallback --
    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        year_by_month = self._tab_year_map(soup)
        events: dict[str, Event] = {}
        for li in soup.select('ul.event_detail_list li[id^="detail-"]'):
            ev = self._parse_card(li, year_by_month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _tab_year_map(soup) -> dict[int, int]:
        """month_number -> year, read from the page's own tab menu."""
        out: dict[int, int] = {}
        for li in soup.select(".event_tab_menu li"):
            yr = li.select_one(".year")
            mo = li.select_one(".month_number")
            if not (yr and mo):
                continue
            try:
                out[int(mo.get_text(strip=True))] = int(yr.get_text(strip=True))
            except ValueError:
                pass
        return out

    def _parse_card(self, li, year_by_month, today) -> Event | None:
        lid = li.get("id", "")
        if not lid.startswith("detail-"):
            return None
        source_url = f"{self.BASE}/event/#{lid}"

        # --- dates ---
        dates = self._card_dates(li, year_by_month, today)
        if not dates:
            return None                     # loud: no parseable date -> drop
        start_date = dates[0]
        end_date = dates[-1] if len(dates) > 1 and dates[-1] != dates[0] else None

        # --- title / subtitle ---
        name_el = li.select_one(".event_name")
        title_ja = _clean(name_el.get_text(" ", strip=True)) if name_el else None
        sub_el = li.select_one(".sub_title")
        subtitle = _clean(sub_el.get_text(" ", strip=True)) if sub_el else None
        subtitle = subtitle or None
        if not title_ja:
            title_ja = subtitle
        if not title_ja:
            return None                     # loud: card with no title -> drop

        # --- table rows (dispatch by <th> label) ---
        time_text = price_td = None
        ticket_url = None
        for tr in li.select("table.detail_table tr"):
            th, td = tr.find("th"), tr.find("td")
            if not th or not td:
                continue
            label = th.get_text(strip=True)
            classes = tr.get("class") or []
            if "公演時間" in label:
                time_text = td.get_text("\n", strip=True)
            elif "料金" in label:
                price_td = td
            elif "url_area" in classes or "公式サイト" in label:
                a = td.find("a", href=True)
                if a:
                    ticket_url = a["href"].strip()

        open_time, start_time = _parse_times(time_text)
        price_text, price_min, is_free = _parse_price(price_td)

        # --- category (mixed calendar policy) ---
        classify = " ".join(filter(None, [title_ja, subtitle, time_text]))
        category = (Category.OTHER
                    if tu.is_nonmusic(classify) or VENUE_OTHER_RE.search(classify)
                    else Category.MUSIC)

        return Event(
            source=self.source_id, source_url=source_url,
            title_ja=title_ja, subtitle=subtitle,
            category=category, genres=[],
            start_date=start_date, end_date=end_date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(time_text and tu.SOLD_OUT_RE.search(time_text)),
            ticket_url=ticket_url,
            **VENUE,
        )

    def _card_dates(self, li, year_by_month, today) -> list[str]:
        day_div = li.select_one(".event_day")
        if not day_div:
            return []
        out: list[str] = []
        for sp in day_div.find_all("span"):
            t = sp.get_text(strip=True)
            if not t:
                continue
            m = DAY_RE.search(t)
            if not m:
                continue
            mo, d = int(m.group(1)), int(m.group(2))
            yr = year_by_month.get(mo)
            iso = None
            if yr:
                try:
                    iso = dt.date(yr, mo, d).isoformat()
                except ValueError:
                    iso = None
            else:
                iso = tu.infer_year(mo, d, today)
            if iso:
                out.append(iso)
        out = sorted(set(out))
        return out


# --------------------------------------------------------------------- utils
def _clean(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def _parse_times(text: str | None) -> tuple[str | None, str | None]:
    """Earliest (first in document order) 開場/開演. Sports rows without 開演
    fall back to the TIPOFF time so the fact isn't lost."""
    if not text:
        return None, None
    open_m = KAIJO_RE.search(text)
    start_m = KAIEN_RE.search(text)
    open_time = open_m.group(1) if open_m else None
    start_time = start_m.group(1) if start_m else None
    if not start_time:
        tip = TIPOFF_RE.search(text)
        if tip:
            start_time = tip.group(1)
    return open_time, start_time


def _amount(s: str) -> int | None:
    m = AMOUNT_RE.search(s)
    if not m:
        return None
    digits = (m.group(1) or m.group(2)).replace(",", "").replace("，", "")
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_price(td) -> tuple[str | None, int | None, bool | None]:
    """Return (price_text, price_min, is_free). price_min = the min of each
    tier line's LEADING amount, so additive component lines
    (＋アップグレード ¥5,500 …) can't undercut the real floor."""
    if td is None:
        return None, None, None
    lines = [ln.strip() for ln in td.get_text("\n").split("\n") if ln.strip()]
    joined = " ".join(lines)
    if FREE_RE.search(joined) and _amount(joined) is None:
        return "無料", 0, True
    leads = [a for a in (_amount(ln) for ln in lines) if a is not None]
    if not leads:
        return None, None, None
    text = re.sub(r"\s+", " ", joined).strip()
    return text[:300], min(leads), False
