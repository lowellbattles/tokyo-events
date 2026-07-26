"""Museum scrapers — ART phase build-out beyond the Mori pair (2026-07-26).

Museums, independent operators, one module: each site gets a small class;
they share the kanji date-range parser and the admission detail pass. All
yield Category.ART date-RANGE events (start..end). The pipeline's detail
pass re-fetches each new exhibition's page once and parse_detail lifts the
ADULT (一般) admission / 入場無料 (textutils.parse_admission). Facts only:
title, dates, admission, URL — imagery and curatorial prose stay on the
source site.

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
import html as html_mod
import json
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper
from .mori import parse_date_range as _parse_dotted_range

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


def parse_any_date_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """Kanji range first; dotted mori-style ("2026.2.28 [土] —2026.5.10")
    as fallback — Yamatane and Sompo print dotted dates."""
    start, end = parse_jp_date_range(text)
    if start:
        return start, end
    return _parse_dotted_range(text)


class _MuseumScraper(BaseScraper):
    """Shared skeleton: single listing page, pure parse, range events.

    Detail pass (admission prices): the pipeline re-fetches each new
    exhibition's source_url once and parse_detail lifts the ADULT (一般)
    admission or an 入場無料 statement via textutils.parse_admission —
    the generic music parse_detail (OPEN/START/playguides) must never
    run against museum pages. Subclasses whose source_urls are not
    content pages (OCAG's JS shells) opt back out."""
    supports_detail = True
    LISTING: str = ""
    VENUE: dict = {}

    def scrape(self) -> Iterable[Event]:
        yield from self.parse(self.fetch(self.LISTING))

    def parse_detail(self, html: str, ev: Event) -> Event:
        if ev.price_min is None and ev.is_free is None:
            soup = BeautifulSoup(html, "lxml")
            price, text, free = tu.parse_admission(
                soup.get_text(" ", strip=True))
            ev.price_min, ev.price_text = price, text
            if free:
                ev.is_free = True
        return ev

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


# ===========================================================================
# Ring 2 (2026-07-26): Nezu, Yamatane, Sompo, 21_21 DESIGN SIGHT.
# Checked and out: Tokyo Station Gallery (ejrcf.or.jp WAF 403s our honest
# UA), Watari-um (TLS certificate broken on both hosts — we never disable
# verification), Ghibli Museum (/exhibition/ is a blog-style archive with
# start dates only and years-old entries still listed — current-vs-ended
# is not extractable as fact).
# ===========================================================================
class NezuScraper(_MuseumScraper):
    source_id = "nezu"
    source_name = "Nezu Museum"
    BASE = "https://www.nezu-muse.or.jp"
    #: the year-schedule page carries every run (finished/current/予告)
    #: with per-exhibition view-NNN.html links
    LISTING = "https://www.nezu-muse.or.jp/jp/exhibitions/schedule/"
    VENUE = dict(
        venue_name="根津美術館",
        venue_area="Minami-Aoyama, Minato-ku",
        address="6-5-1 Minami-Aoyama, Minato-ku, Tokyo",
        lat=35.6622, lng=139.7175,
    )

    def parse(self, html: str, today: Optional[str] = None,
              **context) -> list[Event]:
        today = today or dt.date.today().isoformat()
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for item in soup.select("section.item"):
            term = item.select_one("p.term")
            h4 = item.find("h4")
            if term is None or h4 is None:
                continue
            start, end = parse_jp_date_range(term.get_text(" ", strip=True))
            if not start or (end or start) < today:
                continue                  # finished runs stay on the page
            a = h4.find("a", href=True)
            title = _clean(h4.get_text(" ", strip=True))
            if a is None or not title:
                continue
            url = urljoin(self.BASE, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


class YamataneScraper(_MuseumScraper):
    """The /exhibitions/ page mixes three card states: 開催中/次回開催 cards
    (label -open) carry NO date element — their run lives only on the
    detail page (dt 会期 -> dd kanji range) — while archive cards (-closed)
    carry dotted dates inline. scrape() fetches the few -open detail pages
    (bounded) and hands them to the pure parse as a {url: html} map."""
    source_id = "yamatane"
    source_name = "Yamatane Museum of Art"
    LISTING = "https://www.yamatane-museum.jp/exhibitions/"
    VENUE = dict(
        venue_name="山種美術館",
        venue_area="Hiroo, Shibuya-ku",
        address="3-12-36 Hiroo, Shibuya-ku, Tokyo",
        lat=35.6503, lng=139.7183,
    )
    #: politeness cap on -open detail fetches (current + next is 2 today)
    DETAIL_FETCH_CAP = 4

    def scrape(self) -> Iterable[Event]:
        html = self.fetch(self.LISTING)
        pages: dict[str, str] = {}
        for url in self.detail_targets(html)[:self.DETAIL_FETCH_CAP]:
            try:
                pages[url] = self.fetch(url)
            except Exception:
                continue                  # dateless card just gets skipped
        yield from self.parse(html, detail_pages=pages)

    @staticmethod
    def _card_url(card) -> Optional[str]:
        a = card.find("a", href=re.compile(r"/exhibitions/\d{4}/"))
        return a["href"].strip() if a else None

    def detail_targets(self, html: str) -> list[str]:
        """URLs of -open (current/next) cards that lack an inline date."""
        soup = BeautifulSoup(html, "lxml")
        out: list[str] = []
        for card in soup.select("article.o-card-exhibition"):
            if card.select_one(".c-label-category.-open") is None:
                continue
            if card.select_one(".o-card-exhibition__date") is not None:
                continue
            url = self._card_url(card)
            if url and url not in out:
                out.append(url)
        return out

    @staticmethod
    def _kaiki_range(detail_html: str) -> tuple[Optional[str], Optional[str]]:
        soup = BeautifulSoup(detail_html, "lxml")
        for dt_el in soup.find_all("dt"):
            if "会期" in dt_el.get_text():
                dd = dt_el.find_next_sibling("dd")
                if dd is not None:
                    return parse_jp_date_range(dd.get_text(" ", strip=True))
        return None, None

    def parse(self, html: str, detail_pages: Optional[dict] = None,
              today: Optional[str] = None, **context) -> list[Event]:
        today = today or dt.date.today().isoformat()
        detail_pages = detail_pages or {}
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for card in soup.select("article.o-card-exhibition"):
            title_el = card.select_one(".o-card-exhibition__title")
            url = self._card_url(card)
            if title_el is None or url is None:
                continue
            date_el = card.select_one(".o-card-exhibition__date")
            if date_el is not None:
                # archive card, dotted inline: "2026.2.28 [土] —2026.5.10"
                start, end = parse_any_date_range(
                    date_el.get_text(" ", strip=True))
            elif url in detail_pages:
                start, end = self._kaiki_range(detail_pages[url])
            else:
                continue
            # the page doubles as the archive — current/future runs only
            if not start or (end or start) < today:
                continue
            sub = card.select_one(".o-card-exhibition__title-after")
            title = _clean(title_el.get_text(" ", strip=True))
            if sub is not None:
                title = _clean(f"{title} {sub.get_text(' ', strip=True)}")
            if title and url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


class SompoScraper(_MuseumScraper):
    source_id = "sompo"
    source_name = "Sompo Museum of Art"
    LISTING = "https://www.sompo-museum.org/exhibitions/"
    VENUE = dict(
        venue_name="SOMPO美術館",
        venue_area="Nishi-Shinjuku, Shinjuku-ku",
        address="1-26-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo",
        lat=35.6923, lng=139.6946,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        # one "top" (current) block + "next" (upcoming) block(s), each with
        # __subtitle (série prefix) / __title / __date + a detail link
        for kind in ("top", "next"):
            for block in soup.select(
                    f"[class*='p-exhibitions-index-{kind}__inner'], "
                    f"[class*='p-exhibitions-index-{kind}__body']"):
                title_el = block.select_one(
                    f".p-exhibitions-index-{kind}__title")
                date_el = block.select_one(
                    f".p-exhibitions-index-{kind}__date")
                a = block.find("a", href=re.compile(r"/exhibitions/\d{4}/"))
                if title_el is None or date_el is None or a is None:
                    continue
                # dotted dates: "2026.07.11（土） - 08.30（日）"
                start, end = parse_any_date_range(
                    date_el.get_text(" ", strip=True))
                if not start:
                    continue
                sub = block.select_one(
                    f".p-exhibitions-index-{kind}__subtitle")
                title = _clean(title_el.get_text(" ", strip=True))
                if sub is not None:
                    title = _clean(
                        f"{sub.get_text(' ', strip=True)} {title}")
                url = urljoin(self.LISTING, a["href"])
                if title and url not in events:
                    events[url] = self._event(url, title, start, end)
        return list(events.values())


class DesignSightScraper(_MuseumScraper):
    source_id = "design_sight_2121"
    source_name = "21_21 DESIGN SIGHT"
    BASE = "https://www.2121designsight.jp"
    LISTING = "https://www.2121designsight.jp/program/"
    VENUE = dict(
        venue_name="21_21 DESIGN SIGHT",
        venue_area="Tokyo Midtown, Akasaka, Minato-ku",
        address="9-7-6 Akasaka, Minato-ku, Tokyo",
        lat=35.6668, lng=139.7302,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        # program cards: .summaryArea with an h4 title-link (/program/slug/)
        # and the run as the h5 ("2026年3月27日 (金) - 2026年8月 9日 (日)")
        for area in soup.select("div.summaryArea"):
            h4 = area.find("h4")
            h5 = area.find("h5")
            if h4 is None or h5 is None:
                continue
            a = h4.find("a", href=re.compile(r"^/program/"))
            start, end = parse_jp_date_range(h5.get_text(" ", strip=True))
            title = _clean(h4.get_text(" ", strip=True))
            if a is None or not title or not start:
                continue
            url = urljoin(self.BASE, a["href"])
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


# ===========================================================================
# Ring 3 (2026-07-26): Mitsui Memorial, Panasonic Shiodome, Tokyo
# Photographic Art Museum, Sannomaru Shozokan.
# Checked and out: Idemitsu Museum of Arts (closed for the Teigeki
# building reconstruction - site only lists past exhibitions), Bunkamura
# ザ・ミュージアム (休館中 through the Shibuya renovation; runs off-site
# shows at other venues - revisit on reopening).
# ===========================================================================
#: "2026/7/4(土)〜8/30(日)" - Mitsui prints slash dates; same first-two-hits
#: semantics as the kanji/dotted parsers (start needs the year).
_SLASH_DATE_RE = re.compile(r"(?:(20\d{2})/)?(\d{1,2})/(\d{1,2})")


def _range_from_hits(hits) -> tuple[Optional[str], Optional[str]]:
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
        return None, None
    return start.isoformat(), end.isoformat()


def parse_slash_range(text: str) -> tuple[Optional[str], Optional[str]]:
    return _range_from_hits(_SLASH_DATE_RE.findall(text or ""))


class MitsuiScraper(_MuseumScraper):
    """One exhibition per page: /exhibition/index.html (current) and
    next.html (upcoming), each with p.title + a dl 会期 row (full kanji
    range; p.period's slash dates are the fallback)."""
    source_id = "mitsui"
    source_name = "Mitsui Memorial Museum"
    PAGES = ("https://www.mitsui-museum.jp/exhibition/index.html",
             "https://www.mitsui-museum.jp/exhibition/next.html")
    LISTING = PAGES[0]
    VENUE = dict(
        venue_name="三井記念美術館",
        venue_area="Nihonbashi, Chuo-ku",
        address="Mitsui Main Bldg. 7F, 2-1-1 Nihonbashi-Muromachi, "
                "Chuo-ku, Tokyo",
        lat=35.6866, lng=139.7745,
    )

    def scrape(self) -> Iterable[Event]:
        for i, page in enumerate(self.PAGES):
            try:
                html = self.fetch(page)
            except Exception:
                if i == 0:
                    raise                 # current page down = loud failure
                continue
            yield from self.parse(html, page_url=page)

    def parse(self, html: str, page_url: Optional[str] = None,
              **context) -> list[Event]:
        page_url = page_url or self.LISTING
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.select_one("p.title")
        if title_el is None:
            return []
        title = _clean(title_el.get_text(" ", strip=True))
        start = end = None
        for dt_el in soup.find_all("dt"):
            if _clean(dt_el.get_text()) == "会期":
                dd = dt_el.find_next_sibling("dd")
                if dd is not None:
                    start, end = parse_jp_date_range(
                        dd.get_text(" ", strip=True))
                break
        if not start:
            period = soup.select_one("p.period")
            if period is not None:
                start, end = parse_slash_range(
                    period.get_text(" ", strip=True))
        if not (title and start):
            return []
        return [self._event(page_url, title, start, end)]


class PanasonicShiodomeScraper(_MuseumScraper):
    """/ew/museum/exhibition/ is a meta-refresh hop to the current
    fiscal-year page (26/index.html...), which lists the whole FY:
    h3.exhibition-title (series/title/subtitle spans) + .exhibition-date.
    The 終了致しました label is a template artifact (it appears on future
    shows too) - the today-filter is what drops finished runs."""
    source_id = "panasonic_shiodome"
    source_name = "Panasonic Shiodome Museum of Art"
    HUB = "https://panasonic.co.jp/ew/museum/exhibition/"
    LISTING = HUB
    VENUE = dict(
        venue_name="パナソニック汐留美術館",
        venue_area="Shiodome, Minato-ku",
        address="Panasonic Tokyo Shiodome Bldg. 4F, 1-5-1 "
                "Higashi-Shimbashi, Minato-ku, Tokyo",
        lat=35.6644, lng=139.7614,
    )
    _REFRESH_RE = re.compile(
        r'http-equiv="Refresh"\s+content="\d+;\s*URL=([^"]+)"', re.I)

    def scrape(self) -> Iterable[Event]:
        hub = self.fetch(self.HUB)
        m = self._REFRESH_RE.search(hub)
        if not m:
            return                        # hub shape changed = loud (found=0)
        fy_url = urljoin(self.HUB, m.group(1).strip())
        yield from self.parse(self.fetch(fy_url), page_url=fy_url)

    def parse(self, html: str, page_url: Optional[str] = None,
              today: Optional[str] = None, **context) -> list[Event]:
        page_url = page_url or self.HUB
        today = today or dt.date.today().isoformat()
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for h3 in soup.select("h3.exhibition-title"):
            block = h3
            date_el = a = None
            for _ in range(3):            # date/link live in nearby columns
                block = block.parent
                if block is None:
                    break
                date_el = block.select_one(".exhibition-date")
                a = block.find("a", href=re.compile(r"/museum/exhibition/"))
                if date_el is not None and a is not None:
                    break
            if date_el is None or a is None:
                continue
            start, end = parse_jp_date_range(
                date_el.get_text(" ", strip=True))
            if not start or (end or start) < today:
                continue                  # the FY page keeps finished runs
            title = _clean(h3.get_text(" ", strip=True))
            url = urljoin(page_url, a["href"])
            if title and url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


class TopMuseumScraper(_MuseumScraper):
    """Tokyo Photographic Art Museum: the top page's slider/list anchors
    carry dl.slider__cell blocks - dt em.main is the title, and the dd's
    js-holiday-date spans carry machine-readable data-date attributes
    (dotted text range as fallback). Film screenings (/movie/) are not
    exhibitions and are excluded by the /exhibition/ href filter."""
    source_id = "top_museum"
    source_name = "Tokyo Photographic Art Museum"
    LISTING = "https://topmuseum.jp/"
    VENUE = dict(
        venue_name="東京都写真美術館",
        venue_area="Ebisu Garden Place, Meguro-ku",
        address="1-13-3 Mita, Meguro-ku, Tokyo",
        lat=35.6420, lng=139.7130,
    )

    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.select("a[href*='/exhibition/']"):
            cell = a.select_one("dl.slider__cell")
            if cell is None:
                continue
            main = cell.select_one("dt em.main")
            dd = cell.find("dd")
            if main is None or dd is None:
                continue
            title = _clean(main.get_text(" ", strip=True))
            sub = cell.select_one("dt em.sub")
            if sub is not None and _clean(sub.get_text()):
                title = _clean(f"{title} {sub.get_text(' ', strip=True)}")
            dates = [s["data-date"] for s in dd.select("span[data-date]")
                     if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                     s.get("data-date") or "")]
            if len(dates) >= 2:
                start, end = dates[0], dates[-1]
                if end < start:
                    start = end = None
            else:
                start, end = parse_any_date_range(
                    dd.get_text(" ", strip=True))
            if not (title and start):
                continue
            url = a["href"].split("#")[0].strip()
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())


class ShozokanScraper(_MuseumScraper):
    """Sannomaru Shozokan (Imperial Palace): the exhibitions archive page
    is a JS-filtered shell, but the WordPress REST API exposes the CPT with
    machine-readable exhibition_period_from/to. The feed is the full
    archive back to 2004 -> today-filter; entries whose dates are not yet
    announced (empty period fields, e.g. the 令和8年秋 grand-opening
    special) are skipped until real dates appear. Shows staged at OTHER
    venues (exhibition_location-other-*) are excluded. The museum is
    currently closed ahead of its fall-2026 grand opening, so found=0 is
    legitimate -> allow_empty."""
    source_id = "shozokan"
    source_name = "Sannomaru Shozokan"
    BASE = "https://shozokan.nich.go.jp"
    LISTING = ("https://shozokan.nich.go.jp/wp-json/wp/v2/exhibitions"
               "?per_page=100")
    #: closed between opening phases - a quiet feed is not a breakage
    allow_empty = True
    VENUE = dict(
        venue_name="皇居三の丸尚蔵館",
        venue_area="Imperial Palace East Gardens, Chiyoda-ku",
        address="1-8 Chiyoda, Chiyoda-ku, Tokyo",
        lat=35.6852, lng=139.7594,
    )

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
            start = str(row.get("exhibition_period_from") or "")
            end = str(row.get("exhibition_period_to") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                continue                  # dates not announced yet
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                end = start
            if end < today:
                continue
            locs = [c for c in (row.get("class_list") or [])
                    if str(c).startswith("exhibition_location-")]
            if locs and not any(
                    c.startswith("exhibition_location-shozokan")
                    for c in locs):
                continue                  # staged at another venue
            title = _clean(html_mod.unescape(
                ((row.get("title") or {}).get("rendered")) or ""))
            url = str(row.get("link") or "")
            if not (title and url.startswith("http")):
                continue
            if url not in events:
                events[url] = self._event(url, title, start, end)
        return list(events.values())
