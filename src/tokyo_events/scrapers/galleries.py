"""Gallery / art-space scrapers — the ART phase's gallery ring (2026-07-26).

Same contract as scrapers/museums.py (Category.ART date-range events,
facts only); this module covers non-museum art spaces:

- opera_city_gallery — Tokyo Opera City Art Gallery (Hatsudai). Every
  public page is a JS shell; the content lives at
  /contents/exhibition/current|upcoming (HTML fragments; robots.txt's
  wildcard Disallow for /contents/ is COMMENTED OUT — only Twitterbot/
  facebook are restricted, so we're allowed). One c-exhHeading carries
  the shared run range; items carry the show title + floor. Source-URL
  identity is canonicalized to /ag/exh/detail.php?id=N with N taken from
  the item's image path (/contents/storage/images/exhibition/YYYY/N/…),
  so an exhibition keeps ONE identity as it moves upcoming -> current
  (upcoming items carry no href at all; current vanity URLs like /exh300/
  vary).
- what_museum — WHAT MUSEUM (Warehouse TERRADA, Tennoz). The list page's
  cards carry START dates only; each exhibition's real range lives in a
  th 会期 table row on its detail page -> bounded detail fetches
  (Yamatane pattern). /events/ links (talks) are not exhibitions.
- ggg — ギンザ・グラフィック・ギャラリー (DNP Foundation, Ginza; vclass
  "gallery"). The top page's box-information blocks carry the current
  (and sometimes next) show: h3.ttl-cmn-exhibition span.ttl02 is the
  title (ttl01 is the 第NNN回 series label), p.date the kanji range.

Checked and NOT scrapeable (2026-07-26): TERRADA ART COMPLEX (WAF 403s
our honest UA), Complex665 (domain no longer resolves — the building's
galleries dispersed), POLA Museum Annex (WAF 403), Shiseido Gallery
(news-feed top page, year schedule carries no dated upcoming rows yet —
recheck when the fall show is announced).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Event
from .museums import (_MuseumScraper, _clean, parse_any_date_range,
                      parse_jp_date_range)

_OCAG_IMG_ID_RE = re.compile(r"/images/exhibition/\d{4}/(\d+)/")


class OcagScraper(_MuseumScraper):
    source_id = "opera_city_gallery"
    source_name = "Tokyo Opera City Art Gallery"
    #: detail.php pages are JS shells — nothing there to price-parse
    supports_detail = False
    BASE = "https://www.operacity.jp"
    ENDPOINTS = (
        "https://www.operacity.jp/contents/exhibition/current?lang=ja&home=0",
        "https://www.operacity.jp/contents/exhibition/upcoming?lang=ja&home=0",
    )
    LISTING = ENDPOINTS[0]
    VENUE = dict(
        venue_name="東京オペラシティ アートギャラリー",
        venue_area="Hatsudai, Shinjuku-ku",
        address="Tokyo Opera City Tower 3F, 3-20-2 Nishi-Shinjuku, "
                "Shinjuku-ku, Tokyo",
        lat=35.6835, lng=139.6863,
    )

    def scrape(self) -> Iterable[Event]:
        seen: set[str] = set()
        for i, url in enumerate(self.ENDPOINTS):
            try:
                fragment = self.fetch(url)
            except Exception:
                if i == 0:
                    raise                 # current fragment down = loud
                continue
            for ev in self.parse(fragment):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        # upcoming wraps sections; the current fragment IS one section
        sections = soup.select("section.p-exhList__section") or [soup]
        for sec in sections:
            heading = sec.select_one("h2.c-exhHeading")
            if heading is None:
                continue
            # dotted with fullwidth-bracket weekdays:
            # "2026.07.18［土］ - 09.23［水］" (end inherits the year)
            start, end = parse_any_date_range(
                heading.get_text(" ", strip=True))
            if not start:
                continue
            for item in sec.select(".p-exhPanel__itemBody, .p-exhList__item"):
                # nameMain excludes the floor label (nameSub); the upcoming
                # headerTitle never carries the place to begin with
                name = item.select_one(
                    ".p-exhPanel__nameMain, .p-exhList__headerTitle")
                if name is None:
                    continue
                for br in name.find_all("br"):
                    br.replace_with(" ")
                title = _clean(name.get_text(" ", strip=True))
                if not title:
                    continue
                url = self._identity_url(item)
                if url and url not in events:
                    events[url] = self._event(url, title, start, end)
        return list(events.values())

    def _identity_url(self, item) -> Optional[str]:
        """Stable per-exhibition URL: the numeric id from the item's image
        path -> detail.php?id=N (works for every show, incl. upcoming
        items that have no anchor and vanity-URL current shows)."""
        img = item.find("img", src=True)
        if img is not None:
            m = _OCAG_IMG_ID_RE.search(img["src"])
            if m:
                return f"{self.BASE}/ag/exh/detail.php?id={m.group(1)}"
        a = item.find("a", href=True)
        return urljoin(self.BASE, a["href"]) if a else None


class WhatMuseumScraper(_MuseumScraper):
    source_id = "what_museum"
    source_name = "WHAT MUSEUM"
    BASE = "https://what.warehouseofart.org"
    LISTING = "https://what.warehouseofart.org/exhibitions_events_list/"
    VENUE = dict(
        venue_name="WHAT MUSEUM",
        venue_area="Tennoz, Shinagawa-ku",
        address="2-6-10 Higashi-Shinagawa, Shinagawa-ku, Tokyo",
        lat=35.6221, lng=139.7500,
    )
    #: politeness cap on per-exhibition detail fetches (the museum lists
    #: ~8 concurrent/upcoming shows across its three floors)
    DETAIL_FETCH_CAP = 10
    _TRAILING_DATE_RE = re.compile(r"\s*20\d{2}年\d{1,2}月\d{1,2}日\s*$")

    def scrape(self) -> Iterable[Event]:
        html = self.fetch(self.LISTING)
        pages: dict[str, str] = {}
        for url in self.detail_targets(html)[:self.DETAIL_FETCH_CAP]:
            try:
                pages[url] = self.fetch(url)
            except Exception:
                continue                  # that card just gets skipped
        yield from self.parse(html, detail_pages=pages)

    def detail_targets(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        out: list[str] = []
        for card in soup.select("div.media-sm"):
            a = card.find("a", href=re.compile(r"/exhibitions/[\w-]+/?$"))
            if a is None:
                continue                  # /events/ talks are not exhibitions
            url = urljoin(self.BASE, a["href"])
            if url not in out:
                out.append(url)
        return out

    @staticmethod
    def _kaiki_range(detail_html: str) -> tuple[Optional[str], Optional[str]]:
        """The detail page's 会期 row — th/td on some show templates,
        dt/dd on others (each exhibition gets its own theme). Scoped, so
        the many related-event dates elsewhere on the page can't win."""
        soup = BeautifulSoup(detail_html, "lxml")
        for label in soup.find_all(["th", "dt"]):
            if _clean(label.get_text()) == "会期":
                sib = label.find_next_sibling(
                    "td" if label.name == "th" else "dd")
                if sib is not None:
                    return parse_jp_date_range(sib.get_text(" ", strip=True))
        return None, None

    def parse(self, html: str, detail_pages: Optional[dict] = None,
              today: Optional[str] = None, **context) -> list[Event]:
        import datetime as _dt
        today = today or _dt.date.today().isoformat()
        detail_pages = detail_pages or {}
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for card in soup.select("div.media-sm"):
            a = card.find("a", href=re.compile(r"/exhibitions/[\w-]+/?$"))
            if a is None:
                continue
            url = urljoin(self.BASE, a["href"])
            if url in events or url not in detail_pages:
                continue
            start, end = self._kaiki_range(detail_pages[url])
            if not start:
                continue          # long-term displays without a printed run
            if (end or start) < today:
                continue          # the list keeps finished runs around
            title = self._TRAILING_DATE_RE.sub(
                "", card.get_text(" ", strip=True)).strip()
            title = _clean(title)
            if title:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


class GggScraper(_MuseumScraper):
    source_id = "ggg"
    source_name = "ginza graphic gallery"
    BASE = "https://www.dnpfcp.jp"
    LISTING = "https://www.dnpfcp.jp/gallery/ggg/"
    VENUE = dict(
        venue_name="ギンザ・グラフィック・ギャラリー",
        venue_area="Ginza, Chuo-ku",
        address="DNP Ginza Bldg. 1F/B1F, 7-7-2 Ginza, Chuo-ku, Tokyo",
        lat=35.6690, lng=139.7625,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for box in soup.select("div.box-information"):
            date_el = box.select_one("p.date")
            ttl02 = box.select_one(".ttl-cmn-exhibition .ttl02")
            if date_el is None or ttl02 is None:
                continue
            start, end = parse_jp_date_range(
                date_el.get_text(" ", strip=True))
            if not start:
                continue
            for br in ttl02.find_all("br"):
                br.replace_with(" ")
            title = _clean(ttl02.get_text(" ", strip=True))
            a = box.find("a", href=re.compile(r"schedule/detail\.cgi"))
            if a is None:                 # the 詳細 link sits outside the box
                sec = box.find_parent("section")
                if sec is not None:
                    a = sec.find("a",
                                 href=re.compile(r"schedule/detail\.cgi"))
            url = (urljoin(self.BASE, a["href"]) if a is not None
                   else f"{self.LISTING}#{start}")
            if title and url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())
