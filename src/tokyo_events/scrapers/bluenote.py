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
  REDESIGNED 2026-09 (the old div.scheduleTable/detailsOpen markup and
  artist-slug detail links are gone; this class went from found=47 to
  found=0 overnight). The current template renders a
  <div class="m-schedule-list" data-schedule-panel="list"> of
  .m-schedule-list__row blocks, one per calendar day (consecutive days
  showing the SAME show collapse into one row with several
  .m-schedule-list__date-num day numbers and ONE
  <a class="c-schedule-list-card" href=".../schedule/exec/<id>">
  carrying the title (.c-schedule-list-card__title, <br>-stacked like
  Blue Note Tokyo) and Music Charge price — but no times. Closed days
  render a class="type-closed" <div> (PRIVATE/OFF, no href) instead of
  an <a>; those are skipped.
  Times + per-night sold-out live on the reservation-flow page at
  .../schedule/exec/<id> (fetched via parse_detail — this venue now
  NEEDS a detail pass): one .threeColumnsTypeBg block per night (its
  hidden <input class="e_date" value="YYYYMMDD"> pins the date), each
  holding one .columnAreaBg per stage (1st/2nd) with "Open Hh:MMam/pm /
  Start Hh:MMam/pm" text; a stage that's full carries a "selloutBg"
  class instead of "reservationBtn" (confirmed via the page's own JS:
  `.columnAreaBg.hasClass('selloutBg')`) — no live example was on the
  calendar when this was written, so that path is unit-tested against a
  constructed snippet mirroring the real markup, not a captured fixture.
  A closed reservation window (past shows, or the window not yet open)
  serves an unrelated "ご予約は受付終了" error page instead — NOT sold
  out (受付終了 is deliberately excluded from SOLD_OUT_RE elsewhere in
  this project; a closed window just means parse_detail finds nothing
  to fill and leaves the event as the listing described it).
  There is also a read_event_info/<id> JSON endpoint (backs the
  calendar-view popup) with basic_info/date_range_label/links —
  including a links.detail_page_url that DOES match the old
  www.cottonclubjapan.co.jp/jp/sp/artists/<slug>/ scheme — but its
  schedule_list (meant to carry times) came back empty on every live
  event checked, so it isn't useful for the one thing we actually need.
  Fetching it just to keep the old identity scheme would mean an extra
  request per event on every listing pass (the identity is needed at
  listing time, before we know which events are new/changed), which
  fails the "listing pass stays cheap" architecture and buys nothing
  parse_detail doesn't already need to hit anyway. So identity moves to
  the reservation engine's own exec/<id> URL: one id per booked run
  (confirmed distinct across repeated bookings of the same title — the
  two "コントと音楽 vol.7" weeks in Sept 2026 are ids 5686 and 5687), a
  #YYYY-MM-DD fragment disambiguating each night of a multi-night run
  (yokohama_arena/veats precedent — the fragment is stripped before the
  HTTP GET, so parse_detail's fetch of ev.source_url still lands on the
  one shared exec page and picks its own night out of it).

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
from .base import BaseScraper, NotFoundError
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

#: .../schedule/exec/<id> — the reservation-flow page, also this venue's
#: stable per-run event identity (see the module docstring for why).
_CC_EXEC_ID_RE = re.compile(r"/schedule/exec/(\d+)")


class CottonClubScraper(BaseScraper):
    source_id = "cotton_club"
    source_name = "COTTON CLUB"
    RESERVE = "https://reserve.cottonclubjapan.co.jp"
    #: times + sold-out live on the exec/<id> reservation page now —
    #: the redesigned listing no longer carries them (see module docstring).
    supports_detail = True

    def __init__(self, months_ahead: int = 6, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        first = tu.jst_today().replace(day=1)
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.RESERVE}/reserve/schedule/move/{m.year}{m.month:02d}"
            try:
                html = self.fetch(url)
            except NotFoundError:
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
        listing = soup.find("div", class_="m-schedule-list")
        if listing is None:
            return []                       # structural failure = loud (0)
        if month is None:
            month = tu.jst_today().replace(day=1)
        events: dict[str, Event] = {}
        for row in listing.find_all("div", class_="m-schedule-list__row",
                                     recursive=False):
            for ev in self._parse_row(row, month.year, month.month):
                if ev.source_url not in events:
                    events[ev.source_url] = ev
        return list(events.values())

    def _parse_row(self, row, year: int, mon: int) -> list[Event]:
        # Closed/empty days render a plain <div class="type-closed|
        # type-empty"> (PRIVATE/OFF/nothing yet) with no href — only an
        # <a class="c-schedule-list-card" href=…> is a real booking.
        card = row.find("a", class_="c-schedule-list-card", href=True)
        if card is None:
            return []
        m = _CC_EXEC_ID_RE.search(card["href"])
        if not m:
            return []
        exec_url = f"{self.RESERVE}/reserve/schedule/exec/{m.group(1)}"

        # A run of consecutive nights for the SAME show shares one row and
        # one card, with one .date-num per night ("today" wraps its date-num
        # in a .date-badge instead of .date-item, hence the flatter select).
        days = [int(sp.get_text(strip=True))
                for sp in row.select(".m-schedule-list__date-num")
                if sp.get_text(strip=True).isdigit()]
        if not days:
            return []

        title_el = card.select_one(".c-schedule-list-card__title")
        if not title_el:
            return []
        for br in title_el.find_all("br"):
            br.replace_with("\n")
        lines = [re.sub(r"\s+", " ", x).strip()
                 for x in title_el.get_text().split("\n") if x.strip()]
        if not lines:
            return []
        title = lines[0]
        subtitle = " / ".join(lines[1:]) if len(lines) > 1 else None

        price_min = None
        price_text = None
        price_el = card.select_one(".c-schedule-list-card__charge-price")
        if price_el:
            ym = tu.YEN_RE.search(price_el.get_text())
            if ym:
                price_min = int(re.sub(r"[,，]", "", ym.group(1)))
            charge = card.select_one(".c-schedule-list-card__charge")
            if charge:
                price_text = re.sub(
                    r"\s+", " ", charge.get_text(" ", strip=True)
                ).strip()[:120]

        cat = (Category.OTHER
               if tu.is_nonmusic(f"{title} {subtitle or ''}")
               else Category.MUSIC)

        out: list[Event] = []
        for day in days:
            try:
                date = dt.date(year, mon, day).isoformat()
            except ValueError:
                continue
            # Single-night shows keep the bare exec URL; a multi-night run
            # gets a #date fragment per night (yokohama_arena/veats
            # precedent — stripped before the HTTP GET, so parse_detail's
            # fetch of this URL still lands on the one shared exec page).
            url = exec_url if len(days) == 1 else f"{exec_url}#{date}"
            out.append(Event(
                source=self.source_id, source_url=url,
                title_ja=title, subtitle=subtitle, category=cat,
                start_date=date,
                price_text=price_text, price_min=price_min,
                **COTTON_VENUE,
            ))
        return out

    # --- detail enrichment: per-night times + sold-out from the exec page --
    #
    # The reservation engine is mid-migration (checked 2026-09-24): most
    # exec/<id> pages still serve the OLD flow (.threeColumnsTypeBg per
    # night), but some already serve the NEW one (.c-date-slot-group per
    # night, data-theme="cotton-club" like the redesigned listing) — the
    # split isn't predictable from the event id, so both are tried. A show
    # whose reservation window is closed (past) or not yet open serves a
    # different page again ("ご予約は受付終了" / "予約開始日ご確認") with
    # neither structure — not a failure, just nothing to add.
    def parse_detail(self, html: str, ev: Event) -> Event:
        soup = BeautifulSoup(html, "lxml")
        target = (ev.start_date or "").replace("-", "")  # YYYYMMDD

        for group in soup.select(".c-date-slot-group"):        # NEW flow
            slots = group.select(".c-time-slot")
            radio = slots[0].find("input", attrs={"name": "time-slot"}) \
                if slots else None
            slot_date = (radio["value"].split("_")[0]
                         if radio and radio.get("value") else None)
            if not slots or slot_date != target:
                continue
            if not (ev.open_time or ev.start_time):
                o, s = _earliest_times(group.get_text(" ", strip=True))
                ev.open_time, ev.start_time = o, s
            if not ev.is_sold_out:
                ev.is_sold_out = all(
                    sl.get("data-status") == "sold-out" for sl in slots)
            return ev

        for block in soup.select(".threeColumnsTypeBg"):        # OLD flow
            e_date = block.find("input", class_="e_date")
            if e_date is None or e_date.get("value") != target:
                continue
            stages = block.select(".columnAreaBg")
            if not stages:
                continue
            if not (ev.open_time or ev.start_time):
                o, s = _earliest_times(block.get_text(" ", strip=True))
                ev.open_time, ev.start_time = o, s
            if not ev.is_sold_out:
                ev.is_sold_out = all(
                    "selloutBg" in (st.get("class") or []) for st in stages)
            return ev

        return ev
