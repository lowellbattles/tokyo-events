"""Scraper family for the RUIDO live-house chain — https://ruido.org

In scope: 新宿ReNY (Shinjuku ReNY, ~800 cap, Shinjuku i-Land Tower 2F).
RUIDO runs a family of same-template Webflow sites under ruido.org:
Shinjuku ReNY (/reny/), Akabane ReNY alpha (/akabane_reny/), Yokohama
ReNY (/yokohama_reny/), Nagoya ReNY, RizM, REX, Harajuku, Osaka. Only the
URL prefix (``slug``) changes, so this one class parameterizes by hall and
the family can be extended by adding HALLS entries.

NOTE: the candidate URL "reny.jp" is a WRONG, unrelated e-commerce site —
the real operator site is ruido.org/reny/ (ruido.org has no robots.txt →
404 → no crawl restrictions).

Two-stage shape is unusual here and drives the design:

  * The month-INDEX page /reny/{YYYY}/{M}/index.html (month NOT zero-padded)
    is a pure flyer-thumbnail grid: ``<a href="jul/13.html"><img></a>`` with
    NO visible text — no titles, times, or prices. The only listing facts
    are the event's detail URL and its date (day from the href filename,
    month/year from the page context). Multiple shows on one day get
    non-guessable suffixes (jul/4-1.html, jul/4-2.html), so URLs MUST be
    read from the index hrefs, never constructed from the date.
  * ALL real data lives on the per-event detail page under clean, stable
    Japanese bracket labels: ［TITLE］ ［ACT］ ［OPEN / START］ ［ADV / DOOR］
    ［TICKET］ ［INFO］. parse_detail() is custom and keys off those labels
    (per hard rule #3 — NOT the Webflow ``text-block-NN``/``paragraph-N``
    auto-generated class names, which renumber across the site). It
    OVERWRITES the listing's placeholder title and fills lineup/times/
    prices/ticket links.

So listing events carry a fallback title ("新宿ReNY 7/13公演") that the
pipeline's detail pass replaces with the real ［TITLE］/［ACT］ content.
Structural change fails loud (0 events / empty fields), never silent garbage.
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

HALLS = {
    "reny_shinjuku": dict(
        slug="reny", venue_name="新宿ReNY", venue_area="Shinjuku",
        address="6-5-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo "
                "(Shinjuku i-Land Tower 2F)",
        lat=35.6935, lng=139.6928),
}

# Event detail links on a month-index page look like "jul/13.html",
# "jul/4-2.html", "aug/1-7.html": a lowercase 3-letter month abbrev, the
# day number, an optional "-N" disambiguator, then ".html". Anchoring on
# this text convention (not CSS classes) keeps month-nav / footer / sister
# venue links out.
DETAIL_HREF_RE = re.compile(
    r"(?:^|/)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/"
    r"(\d{1,2})(?:-\d+)?\.html$", re.I)

TIME_RE = re.compile(r"(\d{1,2}:\d{2})")

# Bikou/placeholder values Webflow leaves in unfilled fields.
_PLACEHOLDER = {"", "paragraph", "text"}


def _norm_label(s: str) -> str:
    """Normalize a bracket-label string for matching: drop full/half-width
    brackets and whitespace, upper-case. '［OPEN / START］' -> 'OPEN/START'."""
    return re.sub(r"[［］\[\]\s]", "", s).upper()


class RenyScraper(BaseScraper):
    source_name = "新宿ReNY"
    BASE = "https://ruido.org"
    supports_detail = True          # every event needs its detail page

    def __init__(self, hall_id: str = "reny_shinjuku",
                 months_ahead: int = 3, **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown RUIDO hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        # Walk month-index pages: /reny/{YYYY}/{M}/index.html (M unpadded).
        # These carry only href+date, so they're cheap; the detail pass does
        # the heavy lifting. Stop after two consecutive empty months.
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = self._index_url(m)
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 2:
                break
            yield from fresh

    def _index_url(self, m: dt.date) -> str:
        return f"{self.BASE}/{self.hall['slug']}/{m.year}/{m.month}/index.html"

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse. ``month`` (first-of-month) supplies the
        year+month context the flyer grid lacks; the day comes from each
        detail href. Absolute detail URLs are joined against the month
        directory so multi-suffix same-day events stay distinct."""
        if month is None:
            month = (today or dt.date.today()).replace(day=1)
        base_dir = f"{self.BASE}/{self.hall['slug']}/{month.year}/{month.month}/"
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            m = DETAIL_HREF_RE.search(a["href"])
            if not m:
                continue
            day = int(m.group(2))
            try:
                date = dt.date(month.year, month.month, day).isoformat()
            except ValueError:
                continue                     # bogus day for this month
            url = urljoin(base_dir, a["href"])
            if url in events:
                continue
            events[url] = Event(
                source=self.source_id, source_url=url,
                # placeholder — parse_detail overwrites with real ［TITLE］
                title_ja=f"{self.hall['venue_name']} {month.month}/{day}公演",
                category=Category.MUSIC, start_date=date,
                venue_name=self.hall["venue_name"],
                venue_area=self.hall["venue_area"],
                address=self.hall["address"],
                lat=self.hall["lat"], lng=self.hall["lng"],
            )
        return list(events.values())

    # ----------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill an event from its detail page. Keys off the ［...］ bracket
        labels (stable text convention), NOT Webflow's churny class names.
        Overwrites the listing placeholder title and lineup when the page
        supplies them."""
        soup = BeautifulSoup(html, "lxml")

        title = self._label_text(soup, "TITLE")
        act = self._label_text(soup, "ACT")
        lineup = [s.strip() for s in re.split(r"[／/]", act or "")
                  if s.strip()]
        if lineup:
            ev.lineup = lineup
        if title:
            ev.title_ja = title
        elif lineup:                          # one-man / no separate title
            ev.title_ja = "／".join(lineup)

        times = TIME_RE.findall(self._label_text(soup, "OPEN/START") or "")
        if times:
            ev.open_time = times[0]
            ev.start_time = times[1] if len(times) > 1 else times[0]

        price_val = self._label_text(soup, "ADV/DOOR")
        if price_val and ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(price_val)

        tnode = self._label_node(soup, "TICKET")
        if tnode is not None and not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(
                tnode, tnode.get_text(" ", strip=True))
            for link in ev.ticket_links:
                if link.get("url") and not ev.ticket_url:
                    ev.ticket_url = link["url"]
                    break

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(
                soup.get_text(" ", strip=True)):
            ev.is_sold_out = True
        return ev

    # -- label helpers: find the value <p> that follows a ［LABEL］ marker --
    def _label_node(self, soup, target: str):
        for node in soup.find_all(string=True):
            if _norm_label(str(node)) == target:
                val = node.parent.find_next("p")
                if val is not None:
                    return val
        return None

    def _label_text(self, soup, target: str) -> str | None:
        node = self._label_node(soup, target)
        if node is None:
            return None
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if text.lower() in _PLACEHOLDER:
            return None
        return text
