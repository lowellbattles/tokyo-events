"""Scraper for ヒューリックホール東京 (Hulic Hall Tokyo) — Yurakucho.

Official schedule: https://hulic-theater.com/entertainment/schedules/
(the venue is hulic-theater.com; the similarly named hulic-hall.com is an
unrelated Hulic event-space rental business — do not use it).

Fully static server-rendered WordPress HTML. All facts (date, title,
open/start, price tiers, lineup, ticket vendor) live in the month listing;
there are no per-event detail pages on the venue site (events link out to
external ticket vendors only), so supports_detail is False and the schedule
page itself is the source_url.

Layout of one month page::

    <h3 class="month"><span>July / 07月 </span><span>2026</span></h3>
    <dl id="scheduleline">
      <dt><div class="weekday"><span class="month">07</span>
          <span class="day">10</span><span class="week">Fri</span></div></dt>
      <dd><div>
        <h4>{title}</h4>
        <ul class="schedule">
          <li class="open">Open 18:15｜Start 19:00</li>   (spacing varies)
          <li class="charge">全席指定 ¥4,000（税込）<br>...</li>  (¥ or 円)
          <li class="performer">{artist} / {artist} / ...</li>  (optional)
          <li class="info"><a href="{vendor}">DISK GARAGE</a></li>
        </ul>
      </div></dd>
      ...
    </dl>

Month pagination via the ?d=YYYYMM query param (one page = one calendar
month); prev/next live in <ul id="scheduleNavi">. Low volume (~3/month) is
normal for this hall. Empty future months still return 200 with an empty
<dl>, so the crawl stops after two consecutive empty months.
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

VENUE = dict(
    venue_name="ヒューリックホール東京",
    venue_area="Yurakucho",
    address="東京都千代田区有楽町2-5-1 有楽町マリオン11F",
    lat=35.6747,
    lng=139.7637,
)

# Times: fullwidth ｜ separator, spacing optional ("Open 18:15｜Start 19:00"
# vs "Open15:30｜Start16:00"). tu.parse_times already tolerates the missing
# space, so it is reused directly.

# Prices come as either ¥N,NNN or N,NNN円; take the min across all tiers.
_PRICE_RE = re.compile(r"[¥￥]\s*([\d,，]+)|([\d,，]+)\s*円")
_YEAR_RE = re.compile(r"(20\d{2})")


class HulicHallScraper(BaseScraper):
    source_id = "hulic_hall"
    source_name = "ヒューリックホール東京"
    BASE = "https://hulic-theater.com"
    SCHEDULE_URL = "https://hulic-theater.com/entertainment/schedules/"
    supports_detail = False        # everything is on the listing page

    def __init__(self, months_ahead: int = 4, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = (self.SCHEDULE_URL if i == 0
                   else f"{self.SCHEDULE_URL}?d={m.year}{m.month:02d}")
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            evs = self.parse(html, month=m)
            empty_streak = 0 if evs else empty_streak + 1
            yield from evs
            if empty_streak >= 2:
                break

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        dl = soup.find("dl", id="scheduleline")
        if dl is None:
            return []               # structure changed -> loud failure (0)

        header = soup.find("h3", class_="month")
        header_year = None
        if header is not None:
            ym = _YEAR_RE.search(header.get_text(" ", strip=True))
            if ym:
                header_year = int(ym.group(1))

        events: dict[str, Event] = {}
        frag_seen: dict[str, int] = {}
        for dt_tag in dl.find_all("dt"):
            dd_tag = dt_tag.find_next_sibling("dd")
            if dd_tag is None:
                continue
            ev = self._parse_pair(dt_tag, dd_tag, header_year, month, today)
            if ev is None:
                continue
            # No per-event URLs: every row shares the month page, so make the
            # source_url unique per date (and per collision within a day).
            n = frag_seen.get(ev.start_date, 0) + 1
            frag_seen[ev.start_date] = n
            frag = ev.start_date if n == 1 else f"{ev.start_date}-{n}"
            ym = ev.start_date[:7].replace("-", "")
            ev.source_url = f"{self.SCHEDULE_URL}?d={ym}#{frag}"
            events[ev.source_url] = ev
        return list(events.values())

    def _parse_pair(self, dt_tag, dd_tag, header_year: int | None,
                    month: dt.date | None, today: dt.date | None
                    ) -> Event | None:
        date = self._parse_date(dt_tag, header_year, month, today)
        if not date:
            return None

        h4 = dd_tag.find("h4")
        title = re.sub(r"\s+", " ", h4.get_text(" ", strip=True)) if h4 else ""
        if not title:
            return None

        open_time = start_time = None
        li_open = dd_tag.find("li", class_="open")
        if li_open is not None:
            open_time, start_time = tu.parse_times(li_open.get_text(" ", True))

        price_text = price_min = is_free = None
        li_charge = dd_tag.find("li", class_="charge")
        if li_charge is not None:
            price_text, price_min, is_free = _parse_charge(li_charge)

        lineup: list[str] = []
        li_perf = dd_tag.find("li", class_="performer")
        if li_perf is not None:
            lineup = [s.strip() for s in re.split(
                r"[/／]", li_perf.get_text(" ", strip=True)) if s.strip()]

        ticket_url = None
        ticket_links: list[dict] = []
        li_info = dd_tag.find("li", class_="info")
        if li_info is not None:
            a = li_info.find("a", href=True)
            href = a["href"].strip() if a else ""
            # Guard against the venue's occasional malformed href
            # (e.g. "http://info@jpma-jazz.or.jp") and non-http links.
            if href.startswith(("http://", "https://")) and "@" not in href:
                ticket_url = urljoin(self.BASE, href)
                ticket_links = [{"provider": _provider_for(ticket_url),
                                 "url": ticket_url, "code": None}]

        full_text = dd_tag.get_text(" ", strip=True)
        category = (Category.OTHER
                    if tu.is_nonmusic(f"{title} {' '.join(lineup)}")
                    else Category.MUSIC)

        return Event(
            source=self.source_id,
            source_url=self.SCHEDULE_URL,   # replaced with a unique frag above
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(full_text)),
            ticket_url=ticket_url, ticket_links=ticket_links,
            **VENUE,
        )

    @staticmethod
    def _parse_date(dt_tag, header_year: int | None, month: dt.date | None,
                    today: dt.date | None) -> str | None:
        sp_month = dt_tag.find("span", class_="month")
        sp_day = dt_tag.find("span", class_="day")
        if sp_month is None or sp_day is None:
            return None
        try:
            mo = int(sp_month.get_text(strip=True))
            day = int(sp_day.get_text(strip=True))
        except ValueError:
            return None
        year = header_year if header_year else (month.year if month else None)
        if year is None:
            return tu.infer_year(mo, day, today)
        try:
            return dt.date(year, mo, day).isoformat()
        except ValueError:
            return None


def _parse_charge(li_charge) -> tuple[str | None, int | None, bool | None]:
    """Split the charge <li> on its <br> tags, keep only the lines that
    actually carry a price (¥ or 円), and take the min tier. Notes/policy
    prose (※...) carry no price and are dropped — facts only."""
    lines, cur = [], []
    for node in li_charge.children:
        if getattr(node, "name", None) == "br":
            lines.append("".join(cur))
            cur = []
        else:
            cur.append(node.get_text() if hasattr(node, "get_text")
                       else str(node))
    lines.append("".join(cur))

    price_lines, amounts = [], []
    for ln in lines:
        ln = re.sub(r"\s+", " ", ln).strip()
        found = _PRICE_RE.findall(ln)
        if not found:
            continue
        price_lines.append(ln)
        for yen, en in found:
            num = (yen or en).replace(",", "").replace("，", "")
            if num.isdigit():
                amounts.append(int(num))
    if not amounts:
        return None, None, None
    text = " / ".join(price_lines)[:300]
    pmin = min(amounts)
    return text, pmin, pmin == 0


def _provider_for(url: str) -> str:
    for domain, provider in tu.TICKET_PROVIDERS.items():
        if domain in url:
            return provider
    # Unknown vendor: derive a slug from the registrable-ish hostname
    # (info.diskgarage.com -> "diskgarage").
    m = re.search(r"https?://([^/]+)", url)
    host = (m.group(1) if m else url).split(":")[0]
    parts = [p for p in host.split(".")
             if p not in ("www", "info", "ticket", "tickets")]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "vendor")
