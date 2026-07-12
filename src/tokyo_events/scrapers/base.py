"""Base scraper interface.

Each source (venue site, aggregator) implements one Scraper subclass.
The base class enforces polite scraping defaults:
- identifiable User-Agent
- rate limiting between requests
- timeout + basic retry

Two-stage scraping:
  1. scrape()        — parse listing/index pages -> Events (cheap, every run)
  2. parse_detail()  — parse an event's own page to fill gaps (times, prices,
                       ticket links). The pipeline only calls this for NEW or
                       CHANGED events, so after backfill it's a handful of
                       requests per venue per day.

Etiquette rules for all scrapers in this project:
1. Check robots.txt before adding a new source.
2. Cache aggressively; cap detail fetches per run.
3. Store facts only (titles, dates, prices, URLs). Do not copy prose
   descriptions or images — link to the source instead.
"""

from __future__ import annotations

import codecs
import re
import time
from abc import ABC, abstractmethod
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from ..models import Event
from . import textutils as tu

USER_AGENT = (
    "TokyoEventsAggregator/0.1 (+contact: lowellbattles@gmail.com) "
    "python-requests"
)

_META_CHARSET_RE = re.compile(rb"charset=[\"']?([A-Za-z0-9_.:-]+)", re.I)


def pick_encoding(content_type: str, head: bytes,
                  apparent: str | None, declared: str | None) -> str | None:
    """Decide a response's text encoding. Trust an explicitly declared
    charset (HTTP header, then <meta>) and only fall back to charset
    DETECTION when nothing is declared — chardet occasionally misreads
    CJK pages as Cyrillic, which once mojibake'd a whole scrape."""
    if "charset=" in content_type.lower():
        return declared                    # requests parsed the header
    m = _META_CHARSET_RE.search(head)
    if m:
        try:
            return codecs.lookup(m.group(1).decode("ascii")).name
        except (LookupError, UnicodeDecodeError):
            pass
    return apparent or declared


class BaseScraper(ABC):
    #: unique id, used as Event.source and for the scraper registry
    source_id: str = "base"
    #: human-readable name
    source_name: str = "Base"
    #: seconds to sleep between HTTP requests
    rate_limit_s: float = 2.0
    #: whether the pipeline should fetch detail pages for new/changed events
    supports_detail: bool = True

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request = 0.0

    def fetch(self, url: str, retries: int = 2) -> str:
        """Rate-limited GET returning response text."""
        wait = self.rate_limit_s - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(url, timeout=20)
                self._last_request = time.time()
                resp.raise_for_status()
                resp.encoding = pick_encoding(
                    resp.headers.get("Content-Type", ""),
                    resp.content[:4096],
                    resp.apparent_encoding, resp.encoding)
                return resp.text
            except requests.RequestException as e:  # pragma: no cover
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"[{self.source_id}] failed to fetch {url}: {last_err}")

    @abstractmethod
    def scrape(self) -> Iterable[Event]:
        """Yield normalized Events from listing pages. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def parse(self, html: str, **context) -> list[Event]:
        """Pure listing-parse step, separated from fetching so it can be
        unit-tested against saved HTML fixtures."""
        raise NotImplementedError

    # --- detail enrichment (generic default; override for site specifics) ---
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill missing fields from the event's own page. The default
        implementation covers the near-universal Japanese venue conventions:
        OPEN/START times, ¥ price tiers, playguide links, P/L codes,
        SOLD OUT markers."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        if not (ev.open_time or ev.start_time):
            ev.open_time, ev.start_time = tu.parse_times(text)
        if ev.price_min is None:
            # Only look near ADV/前売/料金 markers to avoid merch prices.
            import re
            m = re.search(r"(?:ADV|前売|料金|TICKET|チケット)(.{0,250})", text,
                          re.I | re.S)
            zone = m.group(1) if m else text
            cut = re.search(r"GOODS|グッズ|物販|INFO|お問い合わせ", zone, re.I)
            if cut:
                zone = zone[:cut.start()]
            ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(zone)
        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev
