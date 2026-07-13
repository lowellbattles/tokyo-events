"""Scraper family for the Blue Note Japan group — jazz/soul live clubs.

Two venues, one operator, but DIFFERENT listing templates, so this module
carries two classes that only share a small English-time helper:

- Blue Note Tokyo (bluenote_tokyo) — https://www.bluenote.co.jp/jp/
  The homepage itself is the schedule: one static <ul id="upcomingData">
  holds the entire forward run (~47 events, several months) with
  machine-readable date-start / date-end / date-private (YYYYMMDD)
  attributes. No pagination. Times/prices/lineup live on the per-artist
  detail pages (/jp/artists/<slug>/), filled by a custom parse_detail.

- COTTON CLUB (cotton_club) — reservation subdomain month pages
  https://reserve.cottonclubjapan.co.jp/reserve/schedule/move/YYYYMM
  One page per calendar month; each show is a <div class="detailsOpen">
  block carrying the price, English open/start times, and a detail link
  whose slug ENCODES the date (/jp/sp/artists/<slug>-YYMMDD/). The listing
  already carries everything, so no detail pass is needed.

Both venues stage TWO sets a night ([1st]/[2nd] or [1st.show]/[2nd.show]).
We emit ONE Event per night/run and capture the EARLIEST (1st set) open/
start pair. Times on both sites are English "Open 5:00pm / Start 6:00pm"
(am/pm 12h), NOT the kanji 開場/開演 convention textutils keys off — hence
the local time parser here.

Facts only: the marketing blurb (intro_txt / span.intro) and images are
never stored — we link out via source_url.
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

# --- English am/pm showtimes, shared by both venues -------------------------
# "Open5:00pm Start6:00pm" (Blue Note, no spaces) and
# "[1st.show] open 5:00pm / start 6:00pm" (Cotton Club) both match.
_OPEN_AMPM_RE = re.compile(r"open\s*(\d{1,2}):(\d{2})\s*([ap])m", re.I)
_START_AMPM_RE = re.compile(r"start\s*(\d{1,2}):(\d{2})\s*([ap])m", re.I)


def _to24(h: str, m: str, ap: str) -> str:
    hh, mm = int(h), int(m)
    ap = ap.lower()
    if ap == "p" and hh != 12:
        hh += 12
    elif ap == "a" and hh == 12:
        hh = 0
    return f"{hh:02d}:{mm:02d}"


def _earliest_times(text: str) -> tuple[str | None, str | None]:
    """Return (open, start) of the FIRST (earliest) set in a showtimes
    block. Both venues list the 1st set before the 2nd, so the first
    open/start match is the earliest."""
    o = _OPEN_AMPM_RE.search(text)
    s = _START_AMPM_RE.search(text)
    return (_to24(*o.groups()) if o else None,
            _to24(*s.groups()) if s else None)


# ===========================================================================
# Blue Note Tokyo
# ===========================================================================
BLUENOTE_VENUE = dict(
    venue_name="Blue Note Tokyo (ブルーノート東京)",
    venue_area="Minami-Aoyama",
    address="Raika Bldg. B1F/B2F, 6-3-16 Minami-Aoyama, Minato-ku, Tokyo",
    lat=35.6626, lng=139.7156,
)

_RESERVE_BN_RE = re.compile(
    r"https?://reserve\.bluenote\.co\.jp/reserve/schedule/show_event_info/\d+/")


def _fmt_ymd(s: str) -> str | None:
    """'20260821' -> '2026-08-21' (None if not 8 digits)."""
    if not (s and re.fullmatch(r"\d{8}", s)):
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
    except ValueError:
        return None


class BlueNoteTokyoScraper(BaseScraper):
    source_id = "bluenote_tokyo"
    source_name = "Blue Note Tokyo"
    BASE = "https://www.bluenote.co.jp"
    LISTING = "https://www.bluenote.co.jp/jp/"
    supports_detail = True

    def scrape(self) -> Iterable[Event]:
        yield from self.parse(self.fetch(self.LISTING))

    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        ul = soup.find(id="upcomingData")
        if ul is None:
            return []                       # structural failure = loud (0)
        events: dict[str, Event] = {}
        for li in ul.find_all("li", attrs={"date-start": True}):
            ev = self._parse_li(li)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_li(self, li) -> Event | None:
        start = _fmt_ymd((li.get("date-start") or "").strip())
        if not start:
            return None
        end = _fmt_ymd((li.get("date-end") or "").strip())
        a = li.select_one("div.name a[href]")
        if not a or not a.get("href"):
            return None
        url = urljoin(self.BASE, a["href"].split("?")[0])

        # Title stacks main / subtitle / lineup on <br>; HTML comments
        # (e.g. <!--38th…-->) are dropped by get_text.
        for br in a.find_all("br"):
            br.replace_with("\n")
        lines = [re.sub(r"\s+", " ", x).strip()
                 for x in a.get_text().split("\n") if x.strip()]
        if not lines:
            return None
        title = lines[0]
        subtitle = " / ".join(lines[1:]) if len(lines) > 1 else None

        cat = (Category.OTHER if tu.is_nonmusic(" ".join(lines))
               else Category.MUSIC)
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, subtitle=subtitle, category=cat,
            start_date=start, end_date=end if end and end != start else None,
            **BLUENOTE_VENUE,
        )

    # --- detail enrichment: English am/pm times, base music charge, lineup --
    def parse_detail(self, html: str, ev: Event) -> Event:
        soup = BeautifulSoup(html, "lxml")
        sections = self._info_sections(soup)

        show = sections.get("DATE & SHOWTIMES")
        if show is not None and not (ev.open_time or ev.start_time):
            o, s = _earliest_times(show.get_text(" ", strip=True))
            ev.open_time, ev.start_time = o, s

        charge = sections.get("MUSIC CHARGE")
        if charge is not None and ev.price_min is None:
            # Headline = the FIRST .price element (base music charge); the
            # per-seat tiers that follow are higher and must not win.
            first = charge.find(class_="price")
            block = first if first is not None else charge
            txt = block.get_text(" ", strip=True)
            m = tu.YEN_RE.search(txt)
            if m:
                ev.price_min = int(re.sub(r"[,，]", "", m.group(1)))
                ev.price_text = re.sub(r"\s+", " ", txt).strip()[:120]

        member = sections.get("MEMBER")
        if member is not None and not ev.lineup:
            ev.lineup = self._lineup(member)

        if not ev.ticket_links:
            m = _RESERVE_BN_RE.search(html)
            if m:
                ev.ticket_links = [{"provider": "bluenote",
                                    "url": m.group(0), "code": None}]

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(
                soup.get_text(" ", strip=True)):
            ev.is_sold_out = True
        return ev

    @staticmethod
    def _info_sections(soup) -> dict:
        """Map infoSection label (h5 <img alt>) -> its content div.right.
        Keys off the alt text, not CSS classes (rule 3)."""
        out: dict[str, object] = {}
        for h5 in soup.find_all("h5"):
            img = h5.find("img", alt=True)
            if not img:
                continue
            block = h5.find_parent("div", class_="infoSection") or h5.parent
            right = block.find("div", class_="right")
            out[img["alt"].strip().upper()] = right or block
        return out

    @staticmethod
    def _lineup(member) -> list[str]:
        """EN performer names from the bilingual MEMBER table (first cell),
        instrument parenthetical stripped for cleaner artist matching."""
        names: list[str] = []
        table = member.find("table")
        if table is None:
            return names
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            en = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True))
            en = re.sub(r"\s*\([^)]*\)\s*$", "", en).strip()
            if en and en not in names:
                names.append(en)
        return names


# ===========================================================================
# COTTON CLUB
# ===========================================================================
COTTON_VENUE = dict(
    venue_name="COTTON CLUB",
    venue_area="Marunouchi",
    address="TOKIA 2F, Tokyo Bldg., 2-7-3 Marunouchi, Chiyoda-ku, Tokyo",
    lat=35.6776, lng=139.7639,
)

_CC_ARTIST_RE = re.compile(r"/jp/sp/artists/")
_CC_SLUG_DATE_RE = re.compile(r"(\d{6})/?$")   # trailing YYMMDD in the slug
# Per-night show date as printed in the details block, e.g. "2026 7.2 thu.".
# The English weekday anchor keeps the reservation-window dates (4/21(火),
# no year) and marketing prose from ever matching.
_CC_DATE_RE = re.compile(
    r"(20\d{2})\s+(\d{1,2})\.(\d{1,2})\s*"
    r"(?:sun|mon|tue|wed|thu|fri|sat)\b", re.I)


class CottonClubScraper(BaseScraper):
    source_id = "cotton_club"
    source_name = "COTTON CLUB"
    RESERVE = "https://reserve.cottonclubjapan.co.jp"
    WWW = "https://www.cottonclubjapan.co.jp"
    #: listing already carries price + times; no detail page needed
    supports_detail = False

    def __init__(self, months_ahead: int = 6, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.RESERVE}/reserve/schedule/move/{m.year}{m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            evs = self.parse(html, month=m)
            if not evs and i > 0:
                break                       # past the schedule horizon
            yield from evs

    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("div", class_="scheduleTable")
        if table is None:
            return []                       # structural failure = loud (0)
        events: dict[str, Event] = {}
        for det in table.find_all("div", class_="detailsOpen"):
            ev = self._parse_details(det)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_details(self, det) -> Event | None:
        a = det.find("a", href=_CC_ARTIST_RE)
        if not a or not a.get("href"):
            return None
        base_url = a["href"].strip()
        if not base_url.startswith("http"):
            base_url = urljoin(self.WWW, base_url)

        block_text = det.get_text(" ", strip=True)
        # Date: the VISIBLE per-night "2026 M.D ddd." string is authoritative.
        # The slug's trailing YYMMDD is only a stable detail-page id — it can
        # differ from the real show date (a two-night run reuses one slug;
        # some slugs even carry the artist's original booking date), so it is
        # only a last-ditch fallback.
        date = None
        dm = _CC_DATE_RE.search(block_text)
        if dm:
            try:
                date = dt.date(int(dm.group(1)), int(dm.group(2)),
                               int(dm.group(3))).isoformat()
            except ValueError:
                date = None
        if not date:
            sm = _CC_SLUG_DATE_RE.search(base_url.rstrip("/"))
            if sm:
                date = _fmt_ymd("20" + sm.group(1))
        if not date:
            return None
        # Multi-night runs share one detail URL; a #date fragment keeps every
        # night's dedupe key unique (yokohama_arena precedent).
        url = f"{base_url}#{date}"

        title = self._title_for(det)
        if not title:
            return None

        price_text, price_min = self._price_for(det)
        open_time, start_time = _earliest_times(block_text)

        cat = (Category.OTHER if tu.is_nonmusic(title)
               else Category.MUSIC)
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=cat, start_date=date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(block_text)),
            **COTTON_VENUE,
        )

    @staticmethod
    def _title_for(det) -> str | None:
        """Nearest preceding scheduleBox title (the day's oldBox table sits
        just before this block's priceBox). Stops at the previous event's
        detailsOpen so titles never bleed across events."""
        sib = det
        while True:
            sib = sib.find_previous_sibling()
            if sib is None:
                return None
            if (sib.name == "div"
                    and "detailsOpen" in (sib.get("class") or [])):
                return None                 # crossed into the previous event
            if sib.name == "table":
                t = sib.find("span", class_="title")
                if t and t.get_text(strip=True):
                    return re.sub(r"\s+", " ",
                                  t.get_text(" ", strip=True)).strip()

    @staticmethod
    def _price_for(det) -> tuple[str | None, int | None]:
        sib = det
        while True:
            sib = sib.find_previous_sibling()
            if sib is None:
                return None, None
            if (sib.name == "div"
                    and "detailsOpen" in (sib.get("class") or [])):
                return None, None
            if sib.name == "div" and "priceBox" in (sib.get("class") or []):
                prices = [int(re.sub(r"[,，]", "", s.get_text()))
                          for s in sib.find_all("span", class_="price")
                          if re.search(r"\d", s.get_text())]
                if not prices:
                    return None, None
                text = re.sub(r"\s+", " ", sib.get_text(" ", strip=True))
                return text.strip()[:120], min(prices)
