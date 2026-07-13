"""Scraper for 下北沢CLUB Que — https://clubque.net

A long-running (since 1994) indie/underground live house in Shimokitazawa.
NOTE: the venue moved off UK.PROJECT (ukproject.com/que — now DEAD) to a new
operator with its own WordPress site at https://clubque.net/ as of 2026-07-01.

Listing: monthly archive pages. Current month is served bare at
/schedule/; any month at /schedule/date/{YYYY}/{MM} (prev/next links + an
archive <select> confirm the shape). One ``<article id="entryYYYYMMDD">``
per event day, so the full date is carried in the id itself (semantic
anchor, like Quattro's data-event-date) — the page ``<h1>2026/07</h1>``
header and per-block day number are only fallbacks.

Block shape (verbatim)::

    <article id="entry20260701">
      <a href="https://clubque.net/schedule/15119/">
        <div class="date wed"><p><b>01</b><span>Wed</span></p></div>
        <div class="text">
          <p style="font-size: 1.5rem;">“発明の力!”</p>            <- title-before / series (subtitle)
          <h2>Dope Flamingo｜フーテン族｜Johnny Yoshi Hiro</h2>       <- main title = lineup, acts split by ｜
        </div>
      </a>
    </article>

The site's own detail page confirms this mapping: ``<h1 class="title">`` is
the ｜-joined lineup and ``<h2 class="title-before">`` is the quoted series
name. ``<li class="streaming">配信あり</li>`` flags a simulcast (kept as a
tag). Sold-out is literal text in the heading, e.g. ``…-oneman-【SOLD OUT】``.

Detail page carries times + price only::

    <dl class="schedule-content__openstart"><dt>OPEN／START</dt>
      <dd><p>18:30／19:00</p></dd></dl>
    <dl class="schedule-content__ticket"><dt>チケット</dt><dd><p>[観覧]<br/>
      ADV.￥4,000／DOOR.￥4,500 [+1D]<br/>… LivePocket … イープラス …</p></dd></dl>

OPEN／START uses a FULL-WIDTH slash (／), so the generic OPEN/START regex in
textutils misreads it — hence the custom parse_detail below. Likewise the
LivePocket links are on ``livepocket.jp`` (not the ``t.livepocket.jp`` the
shared extractor knows), so they are picked up explicitly here.
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

#: listing anchor -> detail page: /schedule/{numeric_id}/
DETAIL_HREF_RE = re.compile(r"/schedule/(\d+)/?$")
#: <article id="entryYYYYMMDD"> carries the full date
ENTRY_ID_RE = re.compile(r"^entry(\d{4})(\d{2})(\d{2})$")
#: acts on a co-bill are separated by a full-width vertical bar
ACT_SPLIT_RE = re.compile(r"[｜|]")
#: strip a bracketed SOLD OUT / 完売 marker out of the heading text
SOLD_OUT_MARK_RE = re.compile(r"[【\[]\s*(?:SOLD\s*OUT|完売)\s*[】\]]", re.I)
#: quote characters wrapping the series/night name
QUOTE_STRIP = "“”\"「」『』 　"
#: detail OPEN／START (full-width OR ascii slash) as a fallback to the <dl>
OPEN_START_RE = re.compile(
    r"OPEN\s*[／/]\s*START\s*(\d{1,2}:\d{2})\s*[／/]\s*(\d{1,2}:\d{2})", re.I)


class QueScraper(BaseScraper):
    source_id = "que_shimokitazawa"
    source_name = "下北沢CLUB Que"
    BASE = "https://clubque.net"

    VENUE = dict(
        venue_name="下北沢CLUB Que",
        venue_area="Shimokitazawa",
        address="東京都世田谷区北沢2-5-2 ビッグベンビルB2",
        lat=35.6612, lng=139.6684,
    )

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        """Walk the current month + a few forward archive pages. Future
        months exist in the archive dropdown even when empty, so stop after
        the first month that yields nothing (live houses fill forward
        contiguously)."""
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = (f"{self.BASE}/schedule/" if i == 0
                   else f"{self.BASE}/schedule/date/{m.year}/{m.month:02d}")
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            if not fresh and i > 0:
                break   # reached the not-yet-announced future
            yield from fresh

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse. Keys off ``<article id="entryYYYYMMDD">`` +
        the ``/schedule/{id}/`` href pattern, so a structural change yields
        0 events (loud) rather than garbage."""
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for art in soup.find_all("article", id=ENTRY_ID_RE):
            ev = self._parse_block(art, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_block(self, art, month: dt.date | None,
                     today: dt.date | None) -> Event | None:
        a = art.find("a", href=True)
        if not a:
            return None
        href = a["href"]
        if not DETAIL_HREF_RE.search(href.split("?")[0]):
            return None
        url = urljoin(self.BASE, href)

        date = self._entry_date(art, month, today)
        if not date:
            return None

        text_div = art.find("div", class_="text") or art
        h2 = text_div.find("h2")
        ps = text_div.find_all("p")
        # Site semantics: the <h2> is the main title (the ｜-joined lineup);
        # the first font-size:1.5rem <p> is the "title-before" series name.
        raw_title = h2.get_text(" ", strip=True) if h2 else ""
        subtitle = ps[0].get_text(" ", strip=True) if ps else ""
        if not raw_title:
            # Degenerate block (e.g. title only in the <p>): fall back so we
            # never emit an event with an empty title.
            raw_title, subtitle = subtitle, ""
        if not raw_title:
            return None

        block_text = art.get_text(" ", strip=True)
        is_sold_out = bool(tu.SOLD_OUT_RE.search(block_text))
        title = re.sub(r"\s+", " ", SOLD_OUT_MARK_RE.sub("", raw_title)).strip()
        if not title:
            return None
        subtitle = subtitle.strip(QUOTE_STRIP).strip() or None
        if subtitle == title:
            subtitle = None

        lineup = [p.strip() for p in ACT_SPLIT_RE.split(title) if p.strip()]
        if len(lineup) < 2:
            lineup = []   # single act / event-name row: nothing to split out

        tags = []
        if art.find("li", class_="streaming") or "配信" in block_text:
            tags.append("streaming")

        category = (Category.OTHER if tu.is_nonmusic(f"{title} {subtitle or ''}")
                    else Category.MUSIC)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, subtitle=subtitle,
            category=category, start_date=date,
            lineup=lineup, tags=tags, is_sold_out=is_sold_out,
            venue_name=self.VENUE["venue_name"],
            venue_area=self.VENUE["venue_area"],
            address=self.VENUE["address"],
            lat=self.VENUE["lat"], lng=self.VENUE["lng"],
        )

    def _entry_date(self, art, month: dt.date | None,
                    today: dt.date | None) -> str | None:
        m = ENTRY_ID_RE.match(art.get("id", ""))
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)),
                               int(m.group(3))).isoformat()
            except ValueError:
                pass
        # Fallback: day number in <div class="date"><b>NN</b> + month context.
        b = art.select_one("div.date b")
        if b and b.get_text(strip=True).isdigit():
            day = int(b.get_text(strip=True))
            if month is not None:
                try:
                    return dt.date(month.year, month.month, day).isoformat()
                except ValueError:
                    return None
            return tu.infer_year(dt.date.today().month, day, today)
        return None

    # --------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill times / price / ticket links from the event's own page.

        The theme puts them in dedicated <dl> blocks; OPEN／START uses a
        full-width slash the generic textutils regex can't read, so parse
        it here. Falls back to a full-page slash-aware regex if the <dl>
        churns (missing data, never wrong data).

        Everything is scoped to the main ``<article class="schedule-content">``
        because the page also has a "PICK UP" sidebar listing OTHER events —
        including their 【SOLD OUT】 markers — which must not leak in."""
        soup = BeautifulSoup(html, "lxml")
        main = soup.find("article", class_="schedule-content") or soup
        main_text = main.get_text(" ", strip=True)

        if not (ev.open_time or ev.start_time):
            os_dl = main.find("dl", class_="schedule-content__openstart")
            times: list[str] = []
            if os_dl is not None:
                dd = os_dl.find("dd")
                if dd is not None:
                    times = re.findall(r"\d{1,2}:\d{2}",
                                       dd.get_text(" ", strip=True))
            if len(times) >= 2:
                ev.open_time, ev.start_time = times[0], times[1]
            elif len(times) == 1:
                ev.open_time = ev.start_time = times[0]
            else:
                fb = OPEN_START_RE.search(main_text)
                if fb:
                    ev.open_time, ev.start_time = fb.group(1), fb.group(2)

        tk_dl = main.find("dl", class_="schedule-content__ticket")
        if tk_dl is not None:
            dd = tk_dl.find("dd")
            if dd is not None:
                zone = dd.get_text(" ", strip=True)
                if ev.price_min is None:
                    ev.price_text, ev.price_min, ev.is_free = \
                        tu.parse_prices(zone)
                if not ev.ticket_links:
                    links = tu.extract_ticket_links(dd, zone)
                    # LivePocket lives on livepocket.jp, which the shared
                    # extractor (t.livepocket.jp) misses — add it explicitly.
                    have = {l.get("url") for l in links}
                    for lk in dd.find_all("a", href=True):
                        h = lk["href"]
                        if "livepocket.jp" in h and h not in have:
                            links.append({"provider": "livepocket",
                                          "url": h, "code": None})
                            have.add(h)
                    ev.ticket_links = links

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(main_text):
            ev.is_sold_out = True
        return ev
