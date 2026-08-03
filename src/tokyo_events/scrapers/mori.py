"""Scraper family for Mori Building's Roppongi Hills art spaces — the ART
phase's first sources (2026-07-26).

Two sites, ONE CMS template (identical markup), so one class serves both:
- Mori Art Museum (mori_art_museum) — www.mori.art.museum
- Mori Arts Center Gallery (mori_arts_center_gallery) — macg.roppongihills.com

Listing anatomy (/jp/exhibitions/ and its /en/ mirror):
- Exhibitions are div[class*=module-gridItem] blocks. The site's OWN
  exhibitions carry RELATIVE hrefs (ronmueck/index.html) while cross-promos
  to sibling facilities (Tokyo City View, MACG on the MAM page, the online
  shop, the MAMC membership pitch) are ABSOLUTE URLs — the relative/absolute
  distinction IS the own-vs-foreign filter (rule 3: URL patterns, not CSS).
- Title in .exhibitions-title / .thumbnailList-title; date range in
  .exhibitions-date / .thumbnailList-date: "2026.4.29（水）〜 9.23（水）" (JP),
  "2026.4.29 [Wed] - 9.23 [Wed]" (EN). The end year is omitted unless the
  run crosses a year boundary ("2026.10.31（土）〜 2027.3.28（日）").
- MAM's satellite programs (MAM Collection / Screen / Research) are listed
  WITHOUT dates: they are skipped — no dates on the listing means no event
  facts, and inferring their run from the flagship show would be a guess.
  A detail-pass follow-up can lift their real dates from their own pages.
- Both language mirrors are fetched and joined on the exhibition slug to
  fill title_ja AND title_en; the EN mirror is enrichment and its failure
  never sinks the scrape.

Exhibitions are Category.ART with a date RANGE (start_date..end_date) —
the frontend's art view renders ranges ("on view now"), not day groups.
Music genre facets don't apply (genres stays []). Facts only: titles,
dates, URL — never the artwork imagery or curatorial prose.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper

# "2026.4.29（水）〜 9.23（水）" / "2026.4.29 [Wed] - 9.23 [Wed]" /
# "2026.10.31（土）〜 2027.3.28（日）" — grab dotted dates; the first carries
# the year, the second inherits it unless printed (cross-year runs).
_DOTTED_DATE_RE = re.compile(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})")

_SITES = {
    "mori_art_museum": dict(
        source_name="Mori Art Museum",
        base="https://www.mori.art.museum",
        venue=dict(
            venue_name="森美術館",
            venue_area="Roppongi Hills, Minato-ku",
            address="Roppongi Hills Mori Tower 53F, 6-10-1 Roppongi, "
                    "Minato-ku, Tokyo",
            lat=35.6605, lng=139.7292,
        ),
    ),
    "mori_arts_center_gallery": dict(
        source_name="Mori Arts Center Gallery",
        base="https://macg.roppongihills.com",
        venue=dict(
            venue_name="森アーツセンターギャラリー",
            venue_area="Roppongi Hills, Minato-ku",
            address="Roppongi Hills Mori Tower 52F, 6-10-1 Roppongi, "
                    "Minato-ku, Tokyo",
            lat=35.6605, lng=139.7292,
        ),
    ),
}


def parse_date_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """'2026.4.29（水）〜 9.23（水）' -> ('2026-04-29', '2026-09-23').
    Delegates to tu.range_from_hits — which also closes the gap this
    copy had (R15/SCR-9): it lacked the >3-year sanity guard while being
    the production fallback for yamatane/sompo/top_museum/OCAG."""
    return tu.range_from_hits(_DOTTED_DATE_RE.findall(text or ""))


def parse_items(html: str) -> dict[str, dict]:
    """Pure: one listing page -> {slug: {title, start, end}}.

    Own-site exhibitions only (relative hrefs); undated blocks (satellite
    programs, shop tiles) are dropped. First occurrence of a slug wins —
    the same show can appear in both CURRENT and PICK-UP sections."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict] = {}
    for item in soup.select("[class*=module-gridItem]"):
        a = item.find("a", href=True)
        if a is None:
            continue
        href = a["href"].strip()
        if not href or href.startswith(("http://", "https://", "/", "#")):
            continue                     # cross-promo / shop / membership
        slug = href.split("/")[0]
        if not slug or slug in out:
            continue
        t = item.select_one(".exhibitions-title, .thumbnailList-title")
        d = item.select_one(".exhibitions-date, .thumbnailList-date")
        title = re.sub(r"\s+", " ", t.get_text(" ", strip=True)).strip() \
            if t else ""
        start, end = parse_date_range(d.get_text(" ", strip=True) if d else "")
        if not title or not start:
            continue                     # dateless block = not an event fact
        out[slug] = {"title": title, "start": start, "end": end}
    return out


class MoriMuseumScraper(BaseScraper):
    """One Roppongi Hills art space (see _SITES)."""
    #: detail pass lifts the adult admission from each exhibition page
    supports_detail = True

    def __init__(self, source_id: str, **kw):
        cfg = _SITES[source_id]
        self.source_id = source_id
        self.source_name = cfg["source_name"]
        self.base = cfg["base"]
        self.venue = cfg["venue"]
        super().__init__(**kw)

    def scrape(self) -> Iterable[Event]:
        jp_html = self.fetch(f"{self.base}/jp/exhibitions/")
        try:
            en_html = self.fetch(f"{self.base}/en/exhibitions/")
        except Exception:                # EN mirror is enrichment only
            en_html = None
        yield from self.parse(jp_html, en_html=en_html)

    def parse_detail(self, html: str, ev: Event) -> Event:
        """Admission price only (adult 一般 tier / 入場無料) — the generic
        music detail parser must never touch museum pages."""
        if ev.price_min is None and ev.is_free is None:
            soup = BeautifulSoup(html, "lxml")
            price, text, free = tu.parse_admission(
                soup.get_text(" ", strip=True))
            ev.price_min, ev.price_text = price, text
            if free:
                ev.is_free = True
        return ev

    def parse(self, html: str, en_html: Optional[str] = None,
              **context) -> list[Event]:
        jp = parse_items(html)           # empty -> loud found=0 upstream
        en = parse_items(en_html) if en_html else {}
        events = []
        for slug, item in jp.items():
            en_title = (en.get(slug) or {}).get("title")
            events.append(Event(
                source=self.source_id,
                source_url=urljoin(self.base, f"/jp/exhibitions/{slug}/"),
                title_ja=item["title"],
                title_en=en_title if en_title != item["title"] else None,
                category=Category.ART,
                start_date=item["start"],
                end_date=item["end"] if item["end"] != item["start"] else None,
                **self.venue,
            ))
        return events
