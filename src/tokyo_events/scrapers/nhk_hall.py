"""Scraper for NHKホール (NHK Hall) — Shibuya / Jinnan.

Operator: NHK財団 (NHK Foundation). Official schedule:
https://www.nhk-fdn.or.jp/nhk_hall/event.html

Fully static server-rendered HTML. A SINGLE page carries the whole
forward window (~6 months) — there is no ?ym=/path pagination, so
scrape() makes exactly one fetch. There are no per-event detail pages
(the only outbound links are the organizer's inquiry phone/contact
pages, not ticket-purchase links), so supports_detail is False and the
schedule page itself is the source_url.

Page layout — one <div class="eventBox"> per month::

    <div class="eventBox">
      <div class="header_month">
        <h3><img src="img/month/202607.gif" alt="2026年7月"></h3>   (year+month)
      </div>
      <div class="event_top"><table><thead><tr>
        <th class="celDays">日・曜日</th><th class="celName">催物名</th>
        <th class="celOpen">開場</th><th class="celStart">開演</th>
        <th class="celEnd">終演</th><th class="celAbout">主催・問合せ先</th>
      </tr></thead></table></div>
      <table class="part"><tbody>
        <tr><td class="celDays"><p class="dayText">1日（水）</p></td>
            <td class="celName"><p><strong>{title}</strong></p></td>
            <td class="celOpen">17:30</td><td class="celStart">18:30</td>
            <td class="celEnd">21:00</td>
            <td class="celAbout"> ...<dl><dt>備考：</dt><dd>全席指定 8,000円</dd></dl>
        </td></tr>
      </tbody></table>
      ... (one <table class="part"> per titled run)
    </div>

The current month carries full data (開場/開演/終演 + price in the 備考
notes). Later months use a leaner table (開演(予定) only, contact name +
phone, no price). A multi-day run is one <table class="part"> whose title
cell rowspans several date rows -> emitted as ONE Event with start_date /
end_date, times taken from the first performance.

Robustness: the column classes (celName/celOpen/...) are only a mechanical
join — the parser DERIVES each column's class from the kanji header text
(催物名 / 開場 / 開演 / 終演 / 日・曜日) inside each eventBox's <thead>.
If those kanji headers ever disappear the box yields nothing (loud
found=0), rather than mis-slicing columns. Prices are read only from the
備考 (notes) block, keyed on that kanji, so organizer phone numbers in the
主催 block are never mistaken for a price.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper

VENUE = dict(
    venue_name="NHKホール",
    venue_area="Shibuya",
    address="東京都渋谷区神南2-2-1",
    lat=35.6637,
    lng=139.6962,
)

# Prices come as N,NNN円 (this venue never uses ¥); youth/child tiers may sit
# in parentheses "S 7,300円（3,600円）". Take the min across every tier.
_PRICE_RE = re.compile(r"([\d,，]+)\s*円|[¥￥]\s*([\d,，]+)")
_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
_DAY_RE = re.compile(r"(\d{1,2})\s*日")
_YM_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月")
_YM_SRC_RE = re.compile(r"/month/(20\d{2})(\d{2})\.(?:gif|png|jpe?g)", re.I)

# The site marks non-public bookings (e.g. a closed high-school broadcasting
# contest final) with 一般非公開 in the notes. Like PIA's "PRIVATE" and Club
# Citta's 貸し切り days, these are not attendable public events -> skipped.
# This honours the site's OWN "not public" statement; it is not a broad
# genre-guessing keyword list (category otherwise comes from tu.is_nonmusic).
_NONPUBLIC_RE = re.compile(r"非公開")


class NHKHallScraper(BaseScraper):
    source_id = "nhk_hall"
    source_name = "NHKホール"
    BASE = "https://www.nhk-fdn.or.jp/nhk_hall"
    SCHEDULE_URL = "https://www.nhk-fdn.or.jp/nhk_hall/event.html"
    supports_detail = False        # single listing page holds every fact

    def scrape(self) -> Iterable[Event]:
        # One static page = the entire forward window; no pagination.
        yield from self.parse(self.fetch(self.SCHEDULE_URL))

    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        frag_seen: dict[str, int] = {}
        for box in soup.find_all("div", class_="eventBox"):
            ym = _box_year_month(box)
            if ym is None:
                continue                       # month header gone -> skip box
            roles = _roles_from_thead(box.find("thead"))
            # Structural gate: without the kanji headers we cannot trust the
            # column layout, so refuse to guess (loud found=0 for this box).
            if not {"day", "name", "start"} <= roles.keys():
                continue
            year, mo = ym
            for table in box.find_all("table", class_="part"):
                ev = _parse_table(table, year, mo, roles)
                if ev is None:
                    continue
                # No per-event URLs: every run shares the one schedule page,
                # so give each a unique #date fragment (a numeric suffix keeps
                # two runs that start the same day distinct).
                n = frag_seen.get(ev.start_date, 0) + 1
                frag_seen[ev.start_date] = n
                frag = ev.start_date if n == 1 else f"{ev.start_date}-{n}"
                ev.source_url = f"{self.SCHEDULE_URL}#{frag}"
                events[ev.source_url] = ev
        return list(events.values())


def _box_year_month(box) -> tuple[int, int] | None:
    """Year+month for an eventBox from its header image (alt '2026年7月',
    with the 'month/YYYYMM.gif' src as a fallback)."""
    header = box.find("div", class_="header_month") or box
    for img in header.find_all("img"):
        m = _YM_RE.search(img.get("alt", "") or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    for img in header.find_all("img"):
        m = _YM_SRC_RE.search(img.get("src", "") or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def _roles_from_thead(thead) -> dict[str, str]:
    """Map each logical column -> the class token the site uses for it,
    derived from the kanji <th> labels (not hard-coded class names)."""
    roles: dict[str, str] = {}
    if thead is None:
        return roles
    for th in thead.find_all("th"):
        classes = th.get("class") or []
        if not classes:
            continue
        cls = classes[0]
        txt = th.get_text("", strip=True)
        if "催物名" in txt:
            roles["name"] = cls
        elif "開場" in txt:
            roles["open"] = cls
        elif "開演" in txt:          # "開演" or "開演(予定)"
            roles["start"] = cls
        elif "終演" in txt:
            roles["end"] = cls
        elif "曜日" in txt:          # 日・曜日
            roles["day"] = cls
    return roles


def _parse_table(table, year: int, mo: int,
                 roles: dict[str, str]) -> Event | None:
    rows = table.find_all("tr")
    if not rows:
        return None

    # Skip bookings the site itself flags as not open to the public.
    if _NONPUBLIC_RE.search(table.get_text(" ", strip=True)):
        return None

    # Title: first row carrying the 催物名 cell (later rows of a multi-day
    # run omit it via rowspan).
    name_cls = roles["name"]
    title = None
    for r in rows:
        cell = r.find("td", class_=name_cls)
        if cell is not None:
            title = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if title:
                break
    if not title:
        return None

    # Dates: one per row (a run rowspans the title over several days). All
    # rows in a table belong to the box's month.
    day_cls = roles["day"]
    dates: list[dt.date] = []
    for r in rows:
        dcell = r.find("td", class_=day_cls)
        if dcell is None:
            continue
        m = _DAY_RE.search(dcell.get_text(" ", strip=True))
        if not m:
            continue
        try:
            dates.append(dt.date(year, mo, int(m.group(1))))
        except ValueError:
            continue
    if not dates:
        return None
    dates.sort()
    start_date = dates[0].isoformat()
    end_date = dates[-1].isoformat() if dates[-1] != dates[0] else None

    # Times from the first performance row.
    first = rows[0]
    open_time = _cell_time(first, roles.get("open"))
    start_time = _cell_time(first, roles.get("start"))

    price_text, price_min, is_free = _parse_price(table)

    category = (Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC)

    return Event(
        source="nhk_hall",
        source_url=NHKHallScraper.SCHEDULE_URL,   # unique #frag set by parse()
        title_ja=title, category=category,
        start_date=start_date, end_date=end_date,
        open_time=open_time, start_time=start_time,
        price_text=price_text, price_min=price_min, is_free=is_free,
        **VENUE,
    )


def _cell_time(row, cls: str | None) -> str | None:
    if not cls:
        return None
    cell = row.find("td", class_=cls)
    if cell is None:
        return None
    m = _TIME_RE.search(cell.get_text(" ", strip=True))
    return m.group(1) if m else None


def _parse_price(table) -> tuple[str | None, int | None, bool | None]:
    """Read the price only from the 備考 (notes) block, so organizer phone
    numbers in the 主催 block never masquerade as a fare. Min across tiers."""
    dd = None
    for dl in table.find_all("dl"):
        dt_ = dl.find("dt")
        if dt_ is not None and "備考" in dt_.get_text():
            dd = dl.find("dd")
            break
    if dd is None:
        return None, None, None
    text = re.sub(r"\s+", " ", dd.get_text(" ", strip=True)).strip()
    amounts: list[int] = []
    for yen_kanji, yen_sym in _PRICE_RE.findall(text):
        num = (yen_kanji or yen_sym).replace(",", "").replace("，", "")
        if num.isdigit():
            amounts.append(int(num))
    if not amounts:
        return None, None, None
    pmin = min(amounts)
    return text[:300], pmin, pmin == 0
