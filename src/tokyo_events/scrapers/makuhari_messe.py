"""Scraper for 幕張メッセ (Makuhari Messe) — https://www.m-messe.co.jp

Makuhari Messe is the huge multi-hall convention complex in Chiba
(International Exhibition Halls 1-11, International Conference Hall, and the
幕張イベントホール). Its daily business is trade shows, job fairs, expos and
conferences; concerts (Summer Sonic, Countdown Japan, arena-class one-offs
like GLAY / UNISON SQUARE GARDEN / Vaundy) are the minority.

The official calendar (/event/) mixes every category, but the site exposes a
clean SERVER-SIDE category filter: /event/?c=2 returns only 音楽イベント
(music) rows. We fetch ONLY c=2, so trade shows never enter the DB (their
volume would drown the music feed) — category isolation is done server-side,
not with our own keyword heuristics. c-values seen: 1=イベント, 2=音楽イベント,
3=展示会・見本市, 4=学会・会議, 5=即売会, 6=その他.

Listing (static, server-rendered — no JS): one card per event, keyed off the
detail-URL pattern /event/detail/{id} (bare integer id):
  <li class="eventInr clear"><a href="/event/detail/8865">
    <div class="category"><i class="fas fa-music"></i>音楽イベント</div>
    <div class="date">2026.07.15(水)</div>              # or "… 〜 …" range
    <div class="eventTit">UNISON SQUARE GARDEN「…」<br></div>
Omitting `month` returns EVERY upcoming music event across all future months
on one page, so no month-by-month walk is needed; scrape() just follows the
pager (page=2,3…) if a single page ever overflows, stopping early when a page
is empty (zepp precedent).

Detail (/event/detail/{id}) carries the facts the listing lacks — used by the
detail pass (supports_detail=True):
  <dl class="time"><dd><div class="main">18:30～</div></dd></dl>   # 開場/開演
  <dl class="person">…<div class="price">オールスタンディング(…)\\10,000</div>
  <dl class="url"><dd><a href="https://vintage-rock.com/">…</a></dd>  # promoter
The price ¥ figure is written with a literal backslash (\\10,000, a Shift-JIS
yen glyph) inside free-text tier copy, so the price regex accepts \\ / ¥ / ￥
/ N円. The URL field points at the promoter's / artist's own site (not an
m-messe ticket page) — stored as ticket_url like the tokyo_dome / ariake
arena scrapers; playguide links (rare here) still go through the shared
extract_ticket_links.

Category policy: every c=2 row is already 音楽イベント, kept as Category.MUSIC.
The parser still guards each card by its OWN category label (a non-music label
-> OTHER) plus tu.is_nonmusic on the title as a safety net, so a mislabeled or
mixed page can never silently mint a fake concert.
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

MUSIC_CAT = "音楽イベント"                 # the site's own c=2 category label

_DETAIL_HREF_RE = re.compile(r"/event/detail/(\d+)")
_KAIJO_RE = re.compile(r"開場\s*(\d{1,2}:\d{2})")   # OPEN
_KAIEN_RE = re.compile(r"開演\s*(\d{1,2}:\d{2})")   # START
_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
# Yen figure may be prefixed by ¥ / ￥ / a literal backslash (Shift-JIS yen
# glyph) or suffixed by 円. Scoped to the price tier text only.
_YEN_PREFIX_RE = re.compile(r"[¥￥\\]\s*([\d][\d,，]*)")
_YEN_SUFFIX_RE = re.compile(r"([\d][\d,，]*)\s*円")
_FREE_RE = re.compile(r"入場無料|無料")


def _iso(match: tuple[str, str, str]) -> str | None:
    y, mo, d = (int(x) for x in match)
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def _to_int(s: str) -> int | None:
    try:
        return int(s.replace(",", "").replace("，", ""))
    except ValueError:
        return None


class MakuhariMesseScraper(BaseScraper):
    source_id = "makuhari_messe"
    source_name = "Makuhari Messe"
    BASE = "https://www.m-messe.co.jp"
    #: music-only feed; no `month` param => all upcoming music events at once
    SCHEDULE_URL = "https://www.m-messe.co.jp/event/?c=2"
    supports_detail = True
    MAX_PAGES = 20                 # politeness/safety cap on pager follow

    VENUE = dict(
        venue_name="幕張メッセ",
        venue_area="Makuhari",
        address="2-1 Nakase, Mihama-ku, Chiba-shi, Chiba",
        lat=35.6494, lng=140.0328,
    )

    def scrape(self) -> Iterable[Event]:
        seen: set[str] = set()
        page = 1
        while page <= self.MAX_PAGES:
            url = self.SCHEDULE_URL + (f"&page={page}" if page > 1 else "")
            html = self.fetch(url)
            new_on_page = 0
            for ev in self.parse(html):
                if ev.source_url in seen:
                    continue
                seen.add(ev.source_url)
                new_on_page += 1
                yield ev
            # Stop early: an empty page or one with no more results ends the
            # walk (the single c=2 page is normally the whole feed already).
            if new_on_page == 0 or not self._has_next_page(html):
                break
            page += 1

    def _has_next_page(self, html: str) -> bool:
        soup = BeautifulSoup(html, "lxml")
        nxt = soup.select_one("div.pager li.next") or soup.select_one("li.next")
        if nxt is None:
            return False
        return "disabled" not in (nxt.get("class") or [])

    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        # `today` is accepted for interface symmetry but unused: every date on
        # this site is a full YYYY.MM.DD, so no year inference is needed.
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            if not _DETAIL_HREF_RE.search(a["href"]):
                continue
            url = urljoin(self.BASE, a["href"].split("#")[0].split("?")[0])
            ev = self._parse_card(a, url)
            if ev is not None and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_card(self, a, url: str) -> Event | None:
        # Date: one or two full YYYY.MM.DD tokens ("… 〜 …" = multi-day run).
        date_div = a.find("div", class_="date")
        date_text = date_div.get_text(" ", strip=True) if date_div else \
            a.get_text(" ", strip=True)
        dates = tu.FULL_DATE_RE.findall(date_text)
        if not dates:
            return None                    # loud: unparseable card -> skipped
        start_date = _iso(dates[0])
        if not start_date:
            return None
        end_date = _iso(dates[1]) if len(dates) > 1 else None
        if end_date == start_date:
            end_date = None

        # Title from the card's own heading (entities like &quot; decoded by
        # BeautifulSoup); trailing <br> collapsed away.
        tit_div = a.find("div", class_="eventTit")
        title = re.sub(r"\s+", " ",
                       (tit_div.get_text(" ", strip=True) if tit_div else "")
                       ).strip()
        if not title:
            return None

        cat_div = a.find("div", class_="category")
        cat_text = cat_div.get_text(" ", strip=True) if cat_div else ""
        category = self._categorize(cat_text, title)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, genres=[],
            start_date=start_date, end_date=end_date,
            **self.VENUE,
        )

    @staticmethod
    def _categorize(cat_text: str, title: str) -> Category:
        # Prefer the site's OWN per-row category tag; a non-music label means
        # the page returned a non-music row (shouldn't happen under c=2).
        if cat_text and MUSIC_CAT not in cat_text:
            return Category.OTHER
        # Safety net: a clearly non-concert title is OTHER even under a music
        # label (do not invent broad keyword lists — reuse tu.is_nonmusic).
        if tu.is_nonmusic(title):
            return Category.OTHER
        return Category.MUSIC

    # --- detail enrichment: times / prices / promoter URL from the own page --
    def parse_detail(self, html: str, ev: Event) -> Event:
        soup = BeautifulSoup(html, "lxml")
        detail = soup.select_one("div.eventDetail") or soup

        if not (ev.open_time or ev.start_time):
            time_dd = detail.select_one("dl.time dd")
            ttext = time_dd.get_text(" ", strip=True) if time_dd else ""
            km, sm = _KAIJO_RE.search(ttext), _KAIEN_RE.search(ttext)
            if km:
                ev.open_time = km.group(1)
            if sm:
                ev.start_time = sm.group(1)
            if not (ev.open_time or ev.start_time):
                times = _TIME_RE.findall(ttext)
                if len(times) >= 2:
                    ev.open_time, ev.start_time = times[0], times[1]
                elif times:
                    ev.start_time = times[0]

        if ev.price_min is None:
            price_divs = detail.select("dl.person div.price")
            ptext = " / ".join(d.get_text(" ", strip=True) for d in price_divs
                               if d.get_text(strip=True))
            # Drink/fee notes carry ¥ amounts that must not win the min().
            scan = tu.strip_drink_charges(ptext)
            amounts = [n for n in (
                _to_int(x) for x in
                _YEN_PREFIX_RE.findall(scan) + _YEN_SUFFIX_RE.findall(scan)
            ) if n is not None]
            if amounts:
                ev.price_text = re.sub(r"\s+", " ", ptext).strip()[:300]
                ev.price_min = min(amounts)
                ev.is_free = ev.price_min == 0
            elif ptext and _FREE_RE.search(ptext):
                ev.price_text = re.sub(r"\s+", " ", ptext).strip()[:300]
                ev.price_min = 0
                ev.is_free = True
            # else: no numeric amount (e.g. "公式サイトをご覧ください") ->
            # leave price fields None, matching the arena-scraper convention.

        if not ev.ticket_url:
            url_a = detail.select_one("dl.url dd a[href]")
            if url_a and url_a["href"].startswith("http"):
                ev.ticket_url = url_a["href"]

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(
                soup, detail.get_text(" ", strip=True))
        return ev
