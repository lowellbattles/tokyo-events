"""Scraper for 新代田FEVER — https://www.fever-popo.com

A ~300-cap indie live house in Shindaita (Setagaya-ku), operator brand
"POOTLE". Movable-Type CMS: the monthly schedule page is fully static and
already carries every fact we store (title, OPEN/START, door/adv price,
lineup, ticket links, per-event detail URL) inline — so there is no detail
pass (``supports_detail = False``, yokohama_arena precedent).

Month pages: ``/schedule/YYYY/MM/`` (e.g. /schedule/2026/07/). No single
page holds the future; walk forward month-by-month (zepp precedent),
stopping when a month has no entries.

Per-event structure (verbatim shape from a live 2026-07 fetch)::

    <div id="entry-13539" class="entry-asset asset hentry">
      <div class="asset-header">
        <h2 class="eventtitle">26.07.01 (Wed)&nbsp;GEZAN『Live at 武道館』...</h2>
        <meta property="og:title" content="GEZAN『Live at 武道館』..." />
        <meta property="og:url"   content=".../schedule/2026/07/0119.html" />
      </div>
      <div class="asset-content entry-content"><div class="asset-body">
        <h3><p>artist1<br/>artist2</p></h3>          (lineup, when present)
        <div>OPEN 18:30 / START 19:00</div>
        <div><p>DOOR ￥2000 (+1drink)<br/>※1drink ￥600</p></div>
        ... eplus.jp / livepocket.jp purchase links ...

Parsing conventions this keys off (text, not CSS class names):
- Date: the ``YY.MM.DD (Weekday)`` prefix in the event title (2-digit year
  -> 20YY); the og:url path is the fallback and the detail URL.
- Times: base OPEN/START convention (tu.parse_times).
- Price: the door/adv line carries ADV/DOOR + ¥. The mandatory drink
  surcharge is always a separate line that begins with ``※`` (e.g.
  ``※1drink ￥600``) — those lines are dropped so the drink charge is
  never mistaken for the ticket price, and neither are streaming-ticket
  or note-embedded ¥ amounts (which lack an ADV/DOOR/一般 keyword).
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

# "26.07.01 (Wed) ..." at the head of the event title -> (yy, mm, dd)
TITLE_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s*[（(]")
# Detail URL path: /schedule/2026/07/0119.html  (0119 = day 01, start hour 19)
URL_DATE_RE = re.compile(r"/schedule/(\d{4})/(\d{2})/(\d{2})\d{2}\.html")
# url= / u= params inside the social-share hrefs (robust source_url fallback)
SHARE_URL_RE = re.compile(
    r"[?&](?:url|u)=(https://www\.fever-popo\.com/schedule/\d{4}/\d{2}/\d+\.html)")

# A price line must name a ticket tier; excludes streaming/note ¥ amounts.
PRICE_KW_RE = re.compile(
    r"ADV|DOOR|前売|当日|一般|料金|GUEST|優先|学割|学生|通し|U-?\d", re.I)
# "(+1drink)", "(+2drink)", "(各公演+1drink)" — a drink surcharge suffix.
DRINK_PAREN_RE = re.compile(r"[（(][^)）]*drink[^)）]*[)）]", re.I)

# Ticketing domains the venue links out to for purchases (facts, per rules).
# textutils' map only knows t.livepocket.jp; FEVER uses bare livepocket.jp.
TICKET_DOMAINS = {
    "eplus.jp": "eplus",
    "livepocket.jp": "livepocket",
    "t.livepocket.jp": "livepocket",
    "l-tike.com": "lawson",
    "t.pia.jp": "pia",
    "w.pia.jp": "pia",
    "zaiko.io": "zaiko",
    "tiget.net": "tiget",
    "ticket.rakuten": "rakuten",
}


class FeverScraper(BaseScraper):
    source_id = "fever_shindaita"
    source_name = "新代田FEVER"
    #: everything is on the listing page — no per-event detail fetch.
    supports_detail = False

    BASE = "https://www.fever-popo.com"
    venue_name = "新代田FEVER"
    venue_area = "Shindaita"

    def __init__(self, months_ahead: int = 4, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ---- fetch + delegate (pure parse below) ----
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/schedule/{m.year}/{m.month:02d}/"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break   # month page not published yet -> stop walking
            events = self.parse(html, month=m)
            if not events:
                break   # no entries this month -> assume nothing further out
            yield from events

    # ---- pure parse (html string in, list[Event] out) ----
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for entry in soup.find_all("div", class_="entry-asset"):
            ev = self._parse_entry(entry)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_entry(self, entry) -> Event | None:
        # --- detail URL (canonical source_url) ---
        url = None
        meta_url = entry.find("meta", attrs={"property": "og:url"})
        if meta_url and meta_url.get("content"):
            url = meta_url["content"].strip()
        if not url:                      # fallback: social-share href param
            for a in entry.find_all("a", href=True):
                m = SHARE_URL_RE.search(a["href"])
                if m:
                    url = m.group(1)
                    break
        if not url:
            return None
        url = urljoin(self.BASE, url)

        # --- title (og:title is the clean, date-stripped form) ---
        title = None
        meta_title = entry.find("meta", attrs={"property": "og:title"})
        if meta_title and meta_title.get("content"):
            title = meta_title["content"].strip()
        h2 = entry.find("h2", class_="eventtitle")
        h2_text = h2.get_text(" ", strip=True) if h2 else ""
        if not title and h2_text:        # fallback: strip the date prefix
            title = TITLE_DATE_RE.sub("", h2_text).strip("） )　 ").strip()
        if not title:
            return None

        # --- date: "YY.MM.DD" in the title, else the og:url path ---
        date = None
        dm = TITLE_DATE_RE.search(h2_text)
        if dm:
            yy, mm, dd = (int(g) for g in dm.groups())
            try:
                date = dt.date(2000 + yy, mm, dd).isoformat()
            except ValueError:
                date = None
        if not date:
            um = URL_DATE_RE.search(url)
            if um:
                y, mo, d = (int(g) for g in um.groups())
                try:
                    date = dt.date(y, mo, d).isoformat()
                except ValueError:
                    date = None
        if not date:
            return None

        body = entry.find("div", class_="asset-body")
        body_text = body.get_text("\n", strip=True) if body else ""

        open_time, start_time = tu.parse_times(body_text)
        price_text, price_min, is_free = self._parse_price(body_text)
        lineup = self._parse_lineup(entry)
        ticket_links = self._ticket_links(body) if body else []

        category = (Category.OTHER if tu.is_nonmusic(title)
                    else Category.MUSIC)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time,
            lineup=lineup,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(body_text)),
            ticket_links=ticket_links,
            venue_name=self.venue_name, venue_area=self.venue_area,
        )

    @staticmethod
    def _parse_price(body_text: str):
        """Lowest genuine ticket price. Only lines that (a) do not start with
        ``※`` (drink surcharge / notes) and (b) name a tier (ADV/DOOR/...)
        count, so drink charges, streaming tickets and note-embedded ¥ are
        never picked up as the door price."""
        price_lines = []
        for ln in body_text.splitlines():
            s = ln.strip()
            if not s or s.startswith("※"):
                continue
            if tu.YEN_RE.search(s) and PRICE_KW_RE.search(s):
                price_lines.append(s)
        if not price_lines:
            return None, None, None
        values = " ".join(DRINK_PAREN_RE.sub("", ln) for ln in price_lines)
        yen = [int(x.replace(",", "").replace("，", ""))
               for x in tu.YEN_RE.findall(values)]
        if not yen:
            return None, None, None
        pmin = min(yen)
        return " / ".join(price_lines)[:300], pmin, pmin == 0

    @staticmethod
    def _parse_lineup(entry) -> list[str]:
        """Lineup lives in ``<h3><p>a<br/>b</p></h3>``. h3 can't legally hold a
        <p>, so lxml empties the h3 and re-parents the artist <p>(s) as its
        following siblings — collect those up to the OPEN/START <div>."""
        h3 = entry.find("h3")
        if h3 is None:
            return []
        raw: list[str] = h3.get_text("\n", strip=True).splitlines()
        for sib in h3.next_siblings:
            name = getattr(sib, "name", None)
            if name == "p":
                raw.extend(sib.get_text("\n", strip=True).splitlines())
            elif name == "div":        # reached the OPEN/START block -> done
                break
        out: list[str] = []
        for ln in raw:
            s = ln.strip("　 ").strip()
            if not s:
                continue
            if re.fullmatch(r"[＜<][^＞>]*[＞>]", s):   # "＜Opening Act＞" marker
                continue
            # strip a leading label like "SPECIAL GUEST：" / "ゲスト:"
            lab = re.match(r"^(?:special\s*guest|guest|ゲスト|opening\s*act)"
                           r"\s*[：:]\s*(.+)$", s, re.I)
            if lab:
                s = lab.group(1).strip()
            if s:
                out.append(s)
        return out

    @staticmethod
    def _ticket_links(body) -> list[dict]:
        links, seen = [], set()
        for a in body.find_all("a", href=True):
            href = a["href"]
            for domain, provider in TICKET_DOMAINS.items():
                if domain in href and href not in seen:
                    seen.add(href)
                    links.append({"provider": provider, "url": href,
                                  "code": None})
                    break
        return links
