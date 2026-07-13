"""Scraper for Kアリーナ横浜 / K-Arena Yokohama — https://k-arena.com

A ~20,000-seat, music-DEDICATED arena on the Minato Mirai waterfront
(opened Sep 2023). The operator site is a static, server-rendered
WordPress theme: every event is in the markup, no JS execution needed.

Listing / pagination
--------------------
The schedule lives at /schedule/ (bare = current month) and paginates by a
query param: /schedule/?y=YYYY&m=M  (M is NOT zero-padded, e.g. ?y=2026&m=8
for August). The server renders that month's events into the same static
`schedule-list` markup. scrape() therefore walks months forward with
tu.add_months and stops after a run of empty months (an arena announces
shows only a few months out, so most far-future months are legitimately
empty — that's "no events", not an error).

  NB: WordPress /schedule/page/N/ pagination is a DECOY — it re-renders the
  CURRENT month identically. The only correct forward navigation is ?y=&m=.

Each event is one list item:

    <li class="schedule-list-item">
      <p class="schedule-list-item__date">2026.07.01.Wed.</p>
      <a href="https://k-arena.com/schedule/20260701-1/">
        <h2 class="schedule-list-item__title">... event title ...</h2>
        <p class="schedule-list-item__artist">AND2BLE</p>
        <div class="schedule-list-item__open-start"><p>OPEN 17:30 / START 19:00</p></div>
      </a>
    </li>

The parser keys off the /schedule/YYYYMMDD-n/ detail-URL pattern (the date
is embedded in it — the most robust source) and the OPEN/START text
convention, using the theme class names only as a convenience for locating
the title/artist/time sub-fields. A structural change surfaces as found=0
(loud), never as silent garbage.

Multi-day runs are rendered as SEPARATE per-date list items, each with its
OWN detail URL (…/20260718-1/, …/20260719-1/, …); a second show on the same
day gets -2, etc. So one list item = one single-day event with a unique
source_url and no cross-item grouping is attempted (end_date stays None).

Detail pass
-----------
The listing already carries date/title/artist/OPEN/START — enough to stage.
The detail page adds the ticket price, which the site writes with a 円
SUFFIX ("◆全席指定：12,800円（税込）") rather than a ¥ prefix, under a
"TICKETS" heading and before "NOTES". parse_detail scopes to that TICKETS
zone and takes the min of each ◆-tier line's LEADING amount, so a
note-embedded upgrade component ("指定席16,500円+アップグレード11,000円")
and the booking fee under NOTES ("システム利用料…1000円") can't undercut the
real floor. No standard playguide links appear (purchases go through the
promoter's own application flow), so ticket_links usually stay empty.
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

VENUE = dict(
    venue_name="Kアリーナ横浜",
    venue_area="Minato Mirai",
    address="6-2-14 Minatomirai, Nishi-ku, Yokohama 220-8507",
    lat=35.4655, lng=139.6293,
)

# Detail-URL pattern carries the date: /schedule/20260701-1/ -> 2026-07-01, n=1
DETAIL_HREF_RE = re.compile(r"/schedule/(\d{4})(\d{2})(\d{2})-(\d+)/")

# TICKETS heading … up to the next section heading (NOTES/CONTACT/INFO).
TICKET_ZONE_RE = re.compile(
    r"TICKETS?(.*?)(?:\bNOTES?\b|\bCONTACT\b|\bINFO\b|お問い合わせ|$)",
    re.I | re.S)
# Fee/extra markers that must not be read as a ticket tier, even if they
# somehow sit inside the TICKETS zone.
FEE_CUT_RE = re.compile(r"手数料|システム利用料|送料|ドリンク|GOODS|グッズ|物販")
# Tier bullet used on K-Arena detail pages (◆…：12,800円). Splitting on it
# isolates each tier line so we can take its LEADING amount.
TIER_BULLET_RE = re.compile(r"[◆◇■□●▶・]")
# 12,800円 / ¥12,800 / ￥12,800
AMOUNT_RE = re.compile(r"[¥￥]\s*([\d,，]+)|([\d,，]+)\s*円")


class KArenaScraper(BaseScraper):
    source_id = "k_arena_yokohama"
    source_name = "K-Arena Yokohama"
    BASE = "https://k-arena.com"

    def __init__(self, months_ahead: int = 12, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    def scrape(self) -> Iterable[Event]:
        base = f"{self.BASE}/schedule/"
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            # bare /schedule/ = current month; ?y=&m= (m unpadded) for the rest
            url = base if i == 0 else f"{base}?y={m.year}&m={m.month}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise            # current month must be reachable
                break
            fresh = [e for e in self.parse(html) if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            # Most far-future months are legitimately empty; stop after a run
            # of them rather than walking the whole advertised range.
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 2:
                break
            yield from fresh

    # -- pure parse: html in, Events out (no fetching) --
    def parse(self, html: str, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for a in soup.find_all("a", href=True):
            m = DETAIL_HREF_RE.search(a["href"])
            if not m:
                continue
            url = urljoin(self.BASE, a["href"].split("#")[0])
            li = a.find_parent("li") or a.parent or a
            ev = self._parse_item(li, url, m)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_item(self, li, url: str, href_m: re.Match) -> Event | None:
        # --- date: from the detail-URL digits (authoritative) ---
        try:
            date = dt.date(int(href_m.group(1)), int(href_m.group(2)),
                           int(href_m.group(3))).isoformat()
        except ValueError:
            return None              # malformed date in URL -> drop (loud)

        # --- title: <h2> heading, else the main flyer alt (not the blur) ---
        title = None
        h2 = li.find(["h2", "h3"])
        if h2:
            title = _clean(h2.get_text(" ", strip=True))
        if not title:
            for img in li.find_all("img", alt=True):
                alt = _clean(img.get("alt"))
                if alt and not alt.endswith("- background"):
                    title = alt
                    break
        if not title:
            return None              # titleless card -> drop (loud)

        # --- artist / lineup ---
        artist_el = li.find("p", class_=re.compile("artist"))
        lineup = _artists(artist_el)

        # --- OPEN/START times ---
        os_el = li.find(class_=re.compile("open-start")) or li
        open_time, start_time = tu.parse_times(os_el.get_text(" ", strip=True))

        # --- category (music arena; guard the rare non-concert) ---
        classify = " ".join(filter(None, [title, " ".join(lineup)]))
        category = (Category.OTHER if tu.is_nonmusic(classify)
                    else Category.MUSIC)

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, genres=[],
            start_date=date,
            open_time=open_time, start_time=start_time,
            lineup=lineup,
            **VENUE,
        )

    # -- detail enrichment: add the ticket price (円-suffix, tier-scoped) --
    def parse_detail(self, html: str, ev: Event) -> Event:
        soup = BeautifulSoup(html, "lxml")
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

        if not (ev.open_time or ev.start_time):
            ev.open_time, ev.start_time = tu.parse_times(text)

        if ev.price_min is None:
            price_text, price_min, is_free = _parse_ticket_zone(text)
            if price_min is not None:
                ev.price_text, ev.price_min, ev.is_free = (
                    price_text, price_min, is_free)

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev


# --------------------------------------------------------------------- utils
def _clean(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def _artists(artist_el) -> list[str]:
    """Headliner (+ any 【ゲスト】 guests) as a de-duplicated lineup list.
    <br> and /／、, separate names; 【…】 labels are stripped."""
    if artist_el is None:
        return []
    for br in artist_el.find_all("br"):
        br.replace_with("\n")
    raw = artist_el.get_text("\n", strip=True)
    out: list[str] = []
    for part in re.split(r"[\n/／、,]", raw):
        name = re.sub(r"【[^】]*】", "", part).strip()
        if not name or name.startswith("※"):   # ※… is a note, not an artist
            continue
        if name not in out:
            out.append(name)
    return out


def _amount(s: str) -> int | None:
    m = AMOUNT_RE.search(s)
    if not m:
        return None
    digits = (m.group(1) or m.group(2)).replace(",", "").replace("，", "")
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_ticket_zone(text: str) -> tuple[str | None, int | None, bool | None]:
    """(price_text, price_min, is_free) from a detail page's TICKETS section.

    Scopes to the TICKETS heading (before NOTES/CONTACT), cuts off any
    fee/goods tail, splits into ◆-tier lines and takes the min of each
    line's LEADING amount so additive/upgrade components and booking fees
    can't undercut the real floor. No TICKETS zone -> no price (silent, not
    garbage)."""
    m = TICKET_ZONE_RE.search(text)
    if not m:
        return None, None, None
    zone = m.group(1)
    cut = FEE_CUT_RE.search(zone)
    if cut:
        zone = zone[:cut.start()]
    tiers = [t.strip() for t in TIER_BULLET_RE.split(zone) if t.strip()]
    leads = [a for a in (_amount(t) for t in tiers) if a is not None]
    if not leads:
        return None, None, None
    price_text = re.sub(r"\s+", " ", zone).strip()
    return price_text[:300], min(leads), min(leads) == 0
