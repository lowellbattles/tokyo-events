"""Scraper for DISK GARAGE — https://diskgarage.com

DISK GARAGE is a nationwide concert PROMOTER/presale operator ("チケット
先行 DISK GARAGE") — an earlier source probe skipped this domain as a
ticket-agency aggregation page under hard rule #2 (its detail pages'
actual purchase buttons point out to a third-party playguide, e.g.
eplus.jp), noting heavy overlap with venues already scraped directly
(Zepp group, O-EAST/WEST/Crest, Toyosu PIT, EX THEATER ROPPONGI, ...).
The project owner explicitly overruled that skip 2026-07-15 and asked for
this source to be onboarded on the same footing as sogo_tokyo / creativeman
/ smash_jpn / udo_artists: DISK GARAGE's own calendar is treated as ITS
promoter listing (it books/announces these shows directly, same as the
other promoter sources), not as a scrape of a third-party search page.
Same geography problem as those sources, amplified: the calendar is
nationwide (Kanto rubs shoulders with Osaka/Nagoya/Fukuoka/Kumamoto/
Kanazawa/Toyama on the same day-page) and covers ~60 distinct venues in a
single month — ``venues.resolve_venue`` is therefore an even more load-
bearing gate here than for the other promoter sources: unresolvable venue
strings are dropped and collected (not guessed at with prefecture logic),
and a meaningful slice of the misses are Kanto venues we already scrape
directly under a different alias spelling (e.g. "eggman(渋谷)" vs the
canonical "Shibuya eggman", "DAIKANYAMA UNIT" vs "代官山UNIT",
"渋谷CLUB QUATTRO" vs "Shibuya CLUB QUATTRO") — worth extending
venues._EXTRA_ALIASES with, separately from this change.

Listing: /artist/date/YYYY-MM — one month per page, static HTML, zero-
padded month. Structure is a single ``<div class="l-second-contents-
artist">`` holding, IN DOCUMENT ORDER, one ``<div class="l-second-
contents-artist-date" id="D">`` per calendar day of the month (``id`` is
the bare day-of-month int; days with no shows carry the ``disabled``
class and no child spans) followed immediately by zero-or-more sibling
``<div class="l-second-contents-artist-btn">`` cards for that day:
    <div id="14" class="l-second-contents-artist-date ..." data-color="">
      <span class="ts-h1 eng t-bld">14</span><span class="ts-15">(火)</span>
    </div>
    <div class="l-second-contents-artist-btn wide flex anim">
      <div class="l-second-contents-information-slim-inner ...">
        <a href="/ticket/detail/101239" ...></a>
        <div class="l-second-contents-information-slim-inner-right flex">
          <div class="...-right-inner ..."><span class="ts-h8 t-bld">
            NIGHTMARE</span></div>                        <!-- title -->
          <div class="...-right-inner ..."><span class="ts-h8">
            Spotify O-EAST</span></div>                    <!-- venue -->
          <div class="...-right-inner ..."><span class="ts-h8">
            18:30 開演</span></div>                         <!-- START only -->
        </div>
      </div>
    </div>
The date div's own ``id`` gives the day-of-month directly (no text
parsing needed); we still scan the WHOLE container in document order
(``find_all`` with a predicate matching either card class, not scoped to
direct children) so a nesting-depth change in the day/card wrapper can't
silently detach cards from their date. No OPEN time, price or sold-out
marker appears at listing level — only a bare START time — so a detail
pass is required for open_time/price/ticket_links (``supports_detail =
True``, same shape as smash_jpn).

Detail page (/ticket/detail/<id>) is a labelled outline: successive
``<div class="l-second-contents-inner-inner-contents">`` blocks, each an
``<h3>`` JP field label followed by its value — 公演日 / 会場 / 開演時間 /
券種・料金 / ドリンク代 / 年齢制限 / お問い合わせ, then (in the ticket-
sales section further down the SAME page) 発売日 / 枚数制限 / another
券種・料金 for 当日券情報 (day-of walk-up price, deliberately NOT used
for price_min — it's normally a premium over the advance price). We key
off these JP labels (first occurrence wins), same convention as
sogo_tokyo's ``_outline_rows``, so a CSS-only restyle fails loud rather
than silently reading the wrong block.
- 開演時間: "<p>18:30</p>(17:30 開場)" — START bare, OPEN parenthesized
  next to its own 開場 word (neither matches tu.parse_times(), which
  looks for the Latin OPEN/START words).
- 券種・料金: one or more tiers, EACH shown twice — "¥15,000(税込)" (tax
  included, what a buyer actually pays) immediately followed by
  "¥13,637(税抜)" (tax excluded). Taking a blind min() over every ¥ figure
  in the block (the generic tu.parse_prices behavior) would pick the
  tax-EXCLUDED amount of whichever tier has the lowest excl price, which
  can UNDERCUT another tier's real (tax-included) floor — so price
  parsing here is scoped to explicitly "(税込)"-tagged amounts only, with
  a tu.parse_prices fallback for the rare tier written without the tag.
- チケット発売情報 sales-status badges ("販売中" seen; presumably 完売 /
  受付終了 appear on other events, not sampled) and the eplus.jp purchase
  button are read the normal way (tu.SOLD_OUT_RE / tu.extract_ticket_links).

Parsers key off the ``/ticket/detail/`` URL shape and the site's own JP
field labels, not CSS class names, so a template change fails loud
(found=0) rather than silently parsing nothing.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper

# Detail permalink: /ticket/detail/<numeric id> — the structural key for
# both the listing scan and the fixture URL shape.
DETAIL_HREF_RE = re.compile(r"/ticket/detail/\d+")

# Listing card classes we scan for, in document order, inside the single
# calendar container -- a date card carries the day-of-month in its own
# `id` (no text parsing needed); an event card sits directly after the
# date card it belongs to.
_DATE_CLASS = "l-second-contents-artist-date"
_CARD_CLASS = "l-second-contents-artist-btn"

# Listing card's third info line: "18:30 開演" (START only, no OPEN).
TIME_RE = re.compile(r"(\d{1,2}:\d{2})")

# Detail page "開演時間" block: "18:30 (17:30 開場)" -> (start, open).
TIME_BLOCK_RE = re.compile(r"(\d{1,2}:\d{2}).*?\(\s*(\d{1,2}:\d{2})\s*開場\s*\)")

# Detail page "券種・料金" tiers: only the tax-INCLUDED amount is the real
# floor a buyer pays -- see module docstring for why a blind min() over
# every ¥ figure (incl. the paired 税抜 amount) would be wrong here.
TAX_INCL_YEN_RE = re.compile(r"[¥￥]\s*([\d,]+)\s*[(（]\s*税込\s*[)）]")

# Best-effort "this title is a tour/multi-act billing, not a bare artist
# name" signal, mirroring smash_jpn's convention for this artist-first
# calendar -- excludes it from the guessed lineup. Precision-first: an odd
# multi-act title left as a bare title (no lineup guess) is better than a
# wrong single-name lineup guess.
MULTI_ACT_RE = re.compile(
    r"TOUR|PRESENTS|FES(?:TIVAL)?|\sVS\.?\s|×|\sx\s|&|meets|produced\s+by",
    re.I)


class DiskGarageScraper(BaseScraper):
    source_id = "disk_garage"
    source_name = "DISK GARAGE"
    BASE = "https://diskgarage.com"
    supports_detail = True

    def __init__(self, months_ahead: int = 2, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        #: raw venue strings resolve_venue() couldn't place — distinct,
        #: accumulated across scrape()/parse() calls for operator visibility
        #: (extend venues.CANONICAL/_EXTRA_ALIASES to pick these up).
        self.skipped_venues: set[str] = set()

    # ---------------------------------------------------------------- fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/artist/date/{m.year}-{m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break            # far-future months eventually error out
            for ev in self.parse(html, month=m):
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse: HTML string -> list[Event]. ``month`` pins the
        page's year/month (day cards carry only the bare day-of-month in
        their own ``id``) — scrape() always passes it; without it a date
        can't be resolved, so rows are dropped."""
        if not html or month is None:
            return []
        soup = BeautifulSoup(html, "lxml")
        container = soup.find("div", class_="l-second-contents-artist")
        if container is None:
            return []

        def _is_date_or_card(tag: Tag) -> bool:
            if tag.name != "div":
                return False
            classes = tag.get("class") or []
            return _DATE_CLASS in classes or _CARD_CLASS in classes

        rows: list[tuple[str, str, Event]] = []
        current_day: int | None = None
        for tag in container.find_all(_is_date_or_card):
            classes = tag.get("class") or []
            if _DATE_CLASS in classes:
                day_id = tag.get("id") or ""
                current_day = int(day_id) if day_id.isdigit() else None
                continue
            parsed = self._parse_row(tag, month, current_day)
            if parsed:
                rows.append(parsed)

        # Defensive fragment-on-repeat, mirroring sogo_tokyo/smash_jpn: this
        # platform's own detail ids appear to be one-per-date already (a
        # multi-night run gets a fresh id per night in the sampled data),
        # but guard against a future run-sharing-one-id change the same way.
        dates_by_url: dict[str, set[str]] = defaultdict(set)
        for url, date, _ in rows:
            dates_by_url[url].add(date)

        events: dict[str, Event] = {}
        for url, date, ev in rows:
            surl = f"{url}#{date}" if len(dates_by_url[url]) > 1 else url
            ev.source_url = surl
            if surl not in events:
                events[surl] = ev
        return list(events.values())

    def _parse_row(self, card: Tag, month: dt.date,
                    day: int | None) -> tuple[str, str, Event] | None:
        if day is None:
            return None       # card appeared before any date marker
        try:
            date = dt.date(month.year, month.month, day).isoformat()
        except ValueError:
            return None

        a = card.find("a", href=DETAIL_HREF_RE)
        if a is None:
            return None
        url = urljoin(self.BASE, a["href"])

        lines = [
            _clean(d.get_text(" ", strip=True))
            for d in card.find_all(
                "div", class_="l-second-contents-information-slim-inner-right-inner")
        ]
        lines = [ln for ln in lines if ln]
        if len(lines) < 2:
            return None

        title, venue_raw = lines[0], lines[1]
        if not title or not venue_raw:
            return None

        start_time = None
        if len(lines) >= 3:
            tm = TIME_RE.search(lines[2])
            if tm:
                start_time = tm.group(1)

        # Geography/curation gate: this calendar is nationwide, not
        # Kanto-scoped -- only keep venues venues.py already knows how to
        # place. Do NOT invent prefecture logic here -- collect misses for
        # the operator to extend the registry with instead.
        if resolve_venue(venue_raw) is None:
            self.skipped_venues.add(venue_raw)
            return None

        category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC
        # Artist-first calendar (mirrors smash_jpn's convention): a title
        # with no tour/multi-act markers IS the artist -- feed it into
        # lineup too, letting a future promoters.py wiring match it against
        # the venue's own scraped record.
        lineup = [title] if title and not MULTI_ACT_RE.search(title) else []

        ev = Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            start_time=start_time,
            venue_name=venue_raw, venue_area=None, address=None,
            lat=None, lng=None,
            lineup=lineup,
        )
        return (url, date, ev)

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Read the detail page's JP-labelled outline (see module
        docstring): 開演時間 for OPEN/START, 券種・料金 for price (tax-
        included amounts only), plus the standard playguide-link / sold-out
        scan over the whole page."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        rows = _outline_rows(soup)

        # The listing ALWAYS supplies start_time but NEVER open_time (see
        # module docstring) -- unlike sogo_tokyo/smash_jpn, where open+start
        # arrive together or not at all, gating this fill on "neither is
        # set yet" would mean open_time is never backfilled (start_time is
        # already set from every listing row). Gate on open_time alone, and
        # only let the detail page's start figure in if the listing didn't
        # already supply one.
        if not ev.open_time:
            block = rows.get("開演時間")
            if block is not None:
                zone = block.get_text(" ", strip=True)
                m = TIME_BLOCK_RE.search(zone)
                if m:
                    if not ev.start_time:
                        ev.start_time = m.group(1)
                    ev.open_time = m.group(2)
                elif not ev.start_time:
                    single = TIME_RE.search(zone)
                    if single:
                        ev.start_time = single.group(1)

        if ev.price_min is None:
            block = rows.get("券種・料金")
            if block is not None:
                zone = tu.strip_drink_charges(block.get_text(" ", strip=True))
                ev.price_text, ev.price_min, ev.is_free = _parse_tax_incl_yen(zone)

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev


def _outline_rows(soup: BeautifulSoup) -> dict[str, Tag]:
    """Map the detail page's JP field label (an ``<h3>`` inside each
    ``div.l-second-contents-inner-inner-contents`` block) -> that block.
    The page repeats "券種・料金" once for the real advance tiers and again
    (further down, under 当日券情報) for the day-of walk-up price --
    ``setdefault`` keeps the FIRST occurrence (the real advance tiers),
    same first-wins convention as sogo_tokyo's ``_outline_rows``."""
    out: dict[str, Tag] = {}
    for block in soup.find_all("div", class_="l-second-contents-inner-inner-contents"):
        h3 = block.find("h3")
        if h3 is None:
            continue
        label = _clean(h3.get_text(strip=True))
        out.setdefault(label, block)
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_tax_incl_yen(text: str) -> tuple[str | None, int | None, bool | None]:
    """Parse a 券種・料金 block -> (price_text, price_min, is_free), using
    only explicitly tax-included ("税込") amounts so a tier's paired
    tax-excluded figure can't undercut the real floor (see module
    docstring). Falls back to any ¥ amount if no "税込" tag is present
    (defensive -- a tier written without the tag would otherwise vanish)."""
    if not text:
        return None, None, None
    price_text = _clean(text)[:300] or None
    yen = [int(x.replace(",", "")) for x in TAX_INCL_YEN_RE.findall(text)]
    if not yen:
        yen = [int(x.replace(",", "")) for x in tu.YEN_RE.findall(text)]
    if yen:
        pmin = min(yen)
        return price_text, pmin, pmin == 0
    if re.search(r"無料|入場無料", text):
        return price_text, 0, True
    return price_text, None, None
