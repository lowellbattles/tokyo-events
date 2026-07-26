"""Museum scrapers — ART phase build-out beyond the Mori pair (2026-07-26).

Six museums, six independent operators, one module: each site gets a small
class; they share only the kanji date-range parser. All yield Category.ART
date-RANGE events (start..end), no genres, supports_detail=False (listing
pages carry the full run facts). Facts only: title, dates, URL — imagery
and curatorial prose stay on the source site.

Sources and shapes (all robots-checked 2026-07-26; Suntory Museum of Art
excluded — its WAF 403s our honest UA, and per rule 2 we never bypass):

- tnm  — Tokyo National Museum, www.tnm.jp. The exhibitions list controller
  redirects to the TOP page, so the top page IS the listing: `.inner`
  blocks carrying an `el_label` (展示/予告), an h3.title, and a p.date
  with a kanji range. ピックアップ blocks (kids days etc.) carry other
  labels and are skipped.
- mot  — Museum of Contemporary Art Tokyo. The /exhibitions/ page is a JS
  shell rendered from the PUBLIC feed /json/exhibitions/exhibitions.json:
  items carry machine-readable start/end (YYYYMMDD), title, permalink.
  The feed includes the full archive — only runs ending today or later
  are ingested.
- nact — National Art Center Tokyo, /exhibition_special/: items are
  anchors with year-relative hrefs ("2026/slug/"); dates come from
  `<time datetime="YYYY-MM-DD">` attributes (textual kanji range as
  fallback).
- artizon — Artizon Museum, /exhibition/: a.linkBlockHover cards with
  h3.exhibitionBox__title + p.exhibitionBox__textDate ("2026年6月23日[火]
  - 10月4日[日]", end year omitted within a year). Several concurrent
  floor exhibitions are normal.
- tobikan — Tokyo Metropolitan Art Museum, /exhibition/index.html:
  a.exhibition-item cards (.-title with <br> line breaks, .-period).
  The listing covers the whole fiscal year incl. finished runs — they
  parse fine and the frontend hides ended ones.
- nmwa — National Museum of Western Art, /jp/exhibitions/current.html +
  upcoming.html: section.exb_info blocks (h3 + dd after dt.calendar +
  p.lnk1 link). The permanent-collection block and fuzzy-ended runs
  ("2027年1月中旬予定") carry no parseable range and are skipped — we
  never publish a guessed end date.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper

# "2026年7月14日（火）～2026年9月6日（日）" / "2026年6月23日[火] - 10月4日[日]" /
# "2026年9月 9日（水）" (NACT pads day with a space) — kanji-unit dates; the
# separator (～/〜/－/–/-/―) never needs matching because we just take the
# first two date hits.
_JP_DATE_RE = re.compile(r"(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def parse_jp_date_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """First two kanji dates in `text` -> (start_iso, end_iso).
    The start must carry an explicit year; an end without one inherits it
    (+1 if that would put the end before the start — a year-boundary run).
    Returns (None, None) for anything less than a full, sane range."""
    hits = _JP_DATE_RE.findall(text or "")
    if len(hits) < 2 or not hits[0][0]:
        return None, None
    try:
        start = dt.date(int(hits[0][0]), int(hits[0][1]), int(hits[0][2]))
        ey = int(hits[1][0]) if hits[1][0] else start.year
        end = dt.date(ey, int(hits[1][1]), int(hits[1][2]))
        if not hits[1][0] and end < start:
            end = end.replace(year=ey + 1)
    except ValueError:
        return None, None
    if end < start or (end - start).days > 3 * 365:
        return None, None            # inverted or absurd = not a run
    return start.isoformat(), end.isoformat()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


class _MuseumScraper(BaseScraper):
    """Shared skeleton: single listing page, pure parse, range events."""
    supports_detail = False
    LISTING: str = ""
    VENUE: dict = {}

    def scrape(self) -> Iterable[Event]:
        yield from self.parse(self.fetch(self.LISTING))

    def _event(self, url: str, title: str, start: str, end: Optional[str],
               title_en: Optional[str] = None) -> Event:
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, title_en=title_en, category=Category.ART,
            start_date=start,
            end_date=end if end and end != start else None,
            **self.VENUE,
        )


# ===========================================================================
# Tokyo National Museum
# ===========================================================================
class TnmScraper(_MuseumScraper):
    source_id = "tnm"
    source_name = "Tokyo National Museum"
    LISTING = "https://www.tnm.jp/"
    VENUE = dict(
        venue_name="東京国立博物館",
        venue_area="Ueno Park, Taito-ku",
        address="13-9 Uenokoen, Taito-ku, Tokyo",
        lat=35.7188, lng=139.7765,
    )
    #: exhibition blocks are labeled 展示 (current) or 予告 (upcoming);
    #: anything else (ピックアップ kids days, workshops) is not an exhibition
    _LABELS = {"展示", "予告"}

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for date_p in soup.select("p.date"):
            start, end = parse_jp_date_range(date_p.get_text(" ", strip=True))
            if not start:
                continue
            block = date_p.find_parent(class_="inner")
            if block is None:
                continue
            # current items label via .label_area, 予告 tab items via a bare
            # .el_label_s span; ピックアップ (kids days etc.) fails the text check
            label = block.select_one(".label_area, [class*=el_label]")
            if label is None or _clean(label.get_text()) not in self._LABELS:
                continue
            # the site nests <p class="desc"> INSIDE <h3 class="title"> —
            # invalid HTML that lxml auto-corrects by ejecting the p as a
            # sibling, so the title text lives on p.desc, not the h3
            title_el = block.select_one("p.desc") or block.find("h3")
            # current items link via a 詳細 button inside the block; 予告 items
            # ARE the link (the whole card is one anchor)
            a = (block.find("a", href=re.compile(r"tnm\.jp/modules/r_"))
                 or date_p.find_parent("a", href=True))
            if title_el is None or a is None:
                continue
            title = _clean(title_el.get_text(" ", strip=True))
            url = a["href"].strip()
            if not title or url in events:
                continue
            events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# Museum of Contemporary Art Tokyo (MOT)
# ===========================================================================
class MotScraper(_MuseumScraper):
    source_id = "mot"
    source_name = "Museum of Contemporary Art Tokyo"
    BASE = "https://www.mot-art-museum.jp"
    LISTING = "https://www.mot-art-museum.jp/json/exhibitions/exhibitions.json"
    VENUE = dict(
        venue_name="東京都現代美術館",
        venue_area="Kiyosumi-Shirakawa, Koto-ku",
        address="4-1-1 Miyoshi, Koto-ku, Tokyo",
        lat=35.6797, lng=139.8080,
    )

    @staticmethod
    def _ymd(s: str) -> Optional[str]:
        if not re.fullmatch(r"\d{8}", s or ""):
            return None
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
        except ValueError:
            return None

    def parse(self, payload: str, today: Optional[str] = None,
              **context) -> list[Event]:
        today = today or dt.date.today().isoformat()
        try:
            rows = json.loads(payload)
        except (ValueError, TypeError):
            return []
        if not isinstance(rows, list):
            return []
        events: dict[str, Event] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            start = self._ymd(str(row.get("start") or ""))
            end = self._ymd(str(row.get("end") or ""))
            title = _clean(str(row.get("title") or ""))
            link = str(row.get("permalink") or "")
            # archive feed: only runs still on (or future) become events
            if not (start and end and title and link.startswith("/")):
                continue
            if end < today:
                continue
            url = urljoin(self.BASE, link)
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# National Art Center Tokyo
# ===========================================================================
class NactScraper(_MuseumScraper):
    source_id = "nact"
    source_name = "National Art Center Tokyo"
    LISTING = "https://www.nact.jp/exhibition_special/"
    VENUE = dict(
        venue_name="国立新美術館",
        venue_area="Roppongi, Minato-ku",
        address="7-22-2 Roppongi, Minato-ku, Tokyo",
        lat=35.6654, lng=139.7263,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=re.compile(r"^\d{4}/[\w-]+/?$")):
            h2 = a.find("h2")
            if h2 is None:
                continue
            title = _clean(h2.get_text(" ", strip=True))
            # machine-readable <time datetime="YYYY-MM-DD"> pair, textual
            # kanji range as fallback
            times = [t.get("datetime") for t in a.find_all("time")
                     if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                     t.get("datetime") or "")]
            if len(times) >= 2:
                start, end = times[0], times[1]
                if end < start:
                    start = end = None
            else:
                start, end = parse_jp_date_range(a.get_text(" ", strip=True))
            if not (title and start):
                continue
            url = urljoin(self.LISTING, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# Artizon Museum
# ===========================================================================
class ArtizonScraper(_MuseumScraper):
    source_id = "artizon"
    source_name = "Artizon Museum"
    BASE = "https://www.artizon.museum"
    LISTING = "https://www.artizon.museum/exhibition/"
    VENUE = dict(
        venue_name="アーティゾン美術館",
        venue_area="Kyobashi, Chuo-ku",
        address="1-7-2 Kyobashi, Chuo-ku, Tokyo",
        lat=35.6773, lng=139.7702,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.select("a[href*='/exhibition/detail/']"):
            title_el = a.select_one(".exhibitionBox__title")
            date_el = a.select_one(".exhibitionBox__textDate")
            if title_el is None or date_el is None:
                continue
            title = _clean(title_el.get_text(" ", strip=True))
            start, end = parse_jp_date_range(date_el.get_text(" ", strip=True))
            if not (title and start):
                continue
            url = urljoin(self.BASE, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# Tokyo Metropolitan Art Museum (Tobikan)
# ===========================================================================
class TobikanScraper(_MuseumScraper):
    source_id = "tobikan"
    source_name = "Tokyo Metropolitan Art Museum"
    LISTING = "https://www.tobikan.jp/exhibition/index.html"
    VENUE = dict(
        venue_name="東京都美術館",
        venue_area="Ueno Park, Taito-ku",
        address="8-36 Uenokoen, Taito-ku, Tokyo",
        lat=35.7171, lng=139.7726,
    )

    def parse(self, html: str, today: Optional[str] = None,
              **context) -> list[Event]:
        today = today or dt.date.today().isoformat()
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.select("a.exhibition-item[href]"):
            title_el = a.select_one(".-title")
            period_el = a.select_one(".-period")
            if title_el is None or period_el is None:
                continue
            for br in title_el.find_all("br"):
                br.replace_with(" ")
            title = _clean(title_el.get_text(" ", strip=True))
            start, end = parse_jp_date_range(
                period_el.get_text(" ", strip=True))
            if not (title and start):
                continue
            # the listing is the museum's FULL archive (back to 2012) —
            # only runs still on (or future) become events
            if (end or start) < today:
                continue
            url = urljoin(self.LISTING, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# National Museum of Western Art (NMWA)
# ===========================================================================
class NmwaScraper(_MuseumScraper):
    source_id = "nmwa"
    source_name = "National Museum of Western Art"
    BASE = "https://www.nmwa.go.jp"
    PAGES = ("https://www.nmwa.go.jp/jp/exhibitions/current.html",
             "https://www.nmwa.go.jp/jp/exhibitions/upcoming.html")
    LISTING = PAGES[0]
    VENUE = dict(
        venue_name="国立西洋美術館",
        venue_area="Ueno Park, Taito-ku",
        address="7-7 Uenokoen, Taito-ku, Tokyo",
        lat=35.7154, lng=139.7756,
    )

    def scrape(self) -> Iterable[Event]:
        seen: set[str] = set()
        for i, page in enumerate(self.PAGES):
            try:
                html = self.fetch(page)
            except Exception:
                if i == 0:
                    raise                 # current down = loud failure
                continue                  # upcoming is best-effort extra
            for ev in self.parse(html, page_url=page):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    def parse(self, html: str, page_url: Optional[str] = None,
              **context) -> list[Event]:
        page_url = page_url or self.LISTING
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for sec in soup.select("section.exb_info"):
            h3 = sec.find("h3")
            cal = sec.find("dt", class_="calendar")
            if h3 is None or cal is None:
                continue
            dd = cal.find_next_sibling("dd")
            start, end = parse_jp_date_range(
                dd.get_text(" ", strip=True) if dd else "")
            if not start:
                continue        # permanent collection / fuzzy "中旬予定" runs
            title = _clean(h3.get_text(" ", strip=True))
            a = sec.select_one("p.lnk1 a[href]") or \
                sec.find("a", href=re.compile(r"/jp/exhibitions/"))
            if not title or a is None:
                continue
            url = urljoin(page_url, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())
