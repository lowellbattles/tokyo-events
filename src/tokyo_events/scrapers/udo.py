"""Scraper for UDO ARTISTS (ウドー音楽事務所) — https://www.udo.jp

UDO is a concert PROMOTER (classic-rock / international-artist bookings),
not a venue — a new "promoter aggregator" source alongside sogo_tokyo /
creativeman, but with its OWN two-stage shape:

  1. LISTING (/shows, static HTML): a card per currently-running TOUR,
     linking to a per-show detail page at /shows/<slug>. Cards carry only
     a title, a description blurb and a status badge (受付前/先行期間中/
     一般販売中/受付終了) — NO date, venue or price. There are only ~12
     shows live at a time and no pagination, so every card is followed.

  2. DETAIL (/shows/<slug>): the real schedule. UDO tours often play
     several cities; the page groups dated legs into per-city TABS
     (`s-showsDetail__scheduleTab`, e.g. 東京/大阪/名古屋), each holding
     one-or-more `s-showsDetail__scheduleItem` (one per date) plus a
     shared price block (`s-showsDetail__ticketSummary`, label "チケット
     料金") and shared playguide buttons for that tab.

     UDO's own page lists ONLY the leg(s) UDO itself promotes — e.g.
     GLAY's nationwide 11-venue arena tour shows only its 2 Ariake Arena
     (Tokyo) dates here; the other 9 cities are handled by other local
     promoters and never appear on udo.jp.

     We deliberately do NOT key off the tab LABEL to decide what's in
     scope — a "東京" tab is a hint, not a fact (see venues.resolve_venue
     docstring: promoter calendars often mis/re-name halls, and a handful
     of "Tokyo-labeled" venues UDO plays are halls we have not curated
     yet, e.g. 昭和女子大学人見記念講堂). Instead, every schedule item's
     OWN venue string is the thing that gets resolved through
     venues.resolve_venue; only legs that resolve are kept. This also
     naturally drops non-Kanto legs (Osaka/Nagoya/...) without any
     separate prefecture filter.

  Playguide links are NOT plain <a href> next to each leg — a "詳細"
  button opens a modal (`data-modal-target="<id>"`) whose real content
  (`.s-showsDetail__modal[data-modal-id="<id>"]`) — including the actual
  eplus.jp/t.pia.jp/l-tike.com anchors and any P/Lコード — lives elsewhere
  in the document. We resolve modal ids per-tab and read the referenced
  modal via tu.extract_ticket_links.

Only ~12 shows exist at a time, so scrape() fetches /shows once and then
EVERY show's detail page in the same run (roughly a dozen fetches at
rate_limit_s=2) — supports_detail = False; there is no separate per-event
detail pass, all enrichment happens inline in scrape()/parse_show().

Parsers key off the site's own BEM-ish content classes
(s-showsDetail__scheduleItem / scheduleVenue / scheduleDate / ...) and
visible Japanese labels ("チケット料金"), not incidental styling hooks —
a structural break yields zero events (loud), never silent garbage.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper

SOURCE_ID = "udo_artists"
BASE = "https://www.udo.jp"
SHOWS_URL = f"{BASE}/shows"

# Listing card link: /shows/<slug>, no query/fragment/nested path.
SHOW_HREF_RE = re.compile(r"^/shows/[^/?#]+$")

# "2026年11月29日" (spans may butt against each other or be
# separator-joined by BeautifulSoup depending on how we call get_text()).
YMD_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# Detail-page times are lowercase, English-suffixed: "16:00 open" /
# "17:00 start" — NOT the OPEN/START-prefixed convention tu.parse_times()
# targets, so this source needs its own pair.
TIME_OPEN_RE = re.compile(r"(\d{1,2}:\d{2})\s*open", re.I)
TIME_START_RE = re.compile(r"(\d{1,2}:\d{2})\s*start", re.I)


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


# --------------------------------------------------------------- listing
def parse_shows(html: str) -> list[str]:
    """Pure parse of /shows -> absolute show detail URLs (one per tour
    card). Cards carry no date/venue/price data at all — only the pointer
    to each show's own page, where the real schedule lives."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a.s-showsList__card[href]"):
        href = a["href"]
        if not SHOW_HREF_RE.match(href) or href in seen:
            continue
        seen.add(href)
        urls.append(urljoin(BASE, href))
    return urls


# ----------------------------------------------------------------- detail
def parse_show(html: str, show_url: str,
                skipped: set[str] | None = None) -> list[Event]:
    """Pure parse of one /shows/<slug> page -> zero or more per-date
    Events, one per Kanto-resolvable schedule leg. Non-resolving legs
    (out of scope, or a Kanto hall we have not curated) are dropped; their
    raw venue strings are added to `skipped` when the caller supplies a
    set, purely for operator reporting.

    The artist name is not offered separately anywhere on the page — the
    h1 show title IS the artist/act name in every show observed (GLAY,
    KENNY G, PAUL GILBERT, ...), so it doubles as the sole lineup entry.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1", class_="s-showsDetail__title")
    title = _clean(h1.get_text(" ", strip=True)) if h1 else ""
    if not title:
        return []
    category = Category.OTHER if tu.is_nonmusic(title) else Category.MUSIC

    events: list[Event] = []
    for content in soup.select(".s-showsDetail__scheduleContent"):
        items = content.select(".s-showsDetail__scheduleItem")
        if not items:
            continue
        price_text, price_min, is_free = _tab_price(content)
        ticket_links = _tab_ticket_links(soup, content)
        tab_sold_out = bool(
            tu.SOLD_OUT_RE.search(content.get_text(" ", strip=True)))

        for item in items:
            leg = _parse_item(item)
            if leg is None:
                continue
            venue_raw, date, open_time, start_time = leg
            if resolve_venue(venue_raw) is None:
                if skipped is not None:
                    skipped.add(venue_raw)
                continue
            events.append(Event(
                source=SOURCE_ID,
                source_url=f"{show_url}#{date}",
                title_ja=title,
                category=category,
                start_date=date,
                open_time=open_time, start_time=start_time,
                venue_name=venue_raw,          # RAW; canonicalized at export
                price_text=price_text, price_min=price_min, is_free=is_free,
                is_sold_out=tab_sold_out,
                ticket_links=ticket_links,
                lineup=[title],
            ))
    return events


def _parse_item(item) -> tuple[str, str, str | None, str | None] | None:
    """One s-showsDetail__scheduleItem -> (venue, iso_date, open, start),
    or None if the date/venue can't be read."""
    venue_p = item.find("p", class_="s-showsDetail__scheduleVenue")
    venue = _clean(venue_p.get_text(" ", strip=True)) if venue_p else ""
    if not venue:
        return None

    date_p = item.find("p", class_="s-showsDetail__scheduleDate")
    if date_p is None:
        return None
    m = YMD_RE.search(date_p.get_text(" ", strip=True))
    if not m:
        return None
    try:
        date = dt.date(int(m.group(1)), int(m.group(2)),
                       int(m.group(3))).isoformat()
    except ValueError:
        return None

    times_text = " ".join(
        p.get_text(" ", strip=True)
        for p in item.find_all("p", class_="s-showsDetail__scheduleTime"))
    om = TIME_OPEN_RE.search(times_text)
    sm = TIME_START_RE.search(times_text)
    return venue, date, (om.group(1) if om else None), \
        (sm.group(1) if sm else None)


def _tab_price(content) -> tuple[str | None, int | None, bool | None]:
    """Read the tab's own "チケット料金" block (label-keyed, since a tab
    can also carry 販売情報/VIPパッケージ/クレジット blocks we don't want)."""
    for block in content.select(".s-showsDetail__ticketSummaryBlock"):
        label = block.find(class_="s-showsDetail__ticketSummaryLabel")
        if label is None or _clean(label.get_text(" ", strip=True)) \
                != "チケット料金":
            continue
        body = block.find(class_="s-showsDetail__ticketSummaryContent")
        if body is None:
            return None, None, None
        zone = tu.strip_drink_charges(body.get_text(" ", strip=True))
        return tu.parse_prices(zone)
    return None, None, None


def _tab_ticket_links(soup, content) -> list[dict]:
    """Playguide "詳細" buttons open a modal by id rather than linking
    directly; the modal elsewhere in the document holds the real
    eplus.jp/t.pia.jp/l-tike.com anchors (+ P/Lコード). Resolve each
    button's target id to its modal and read links out of that."""
    links: list[dict] = []
    seen: set[tuple] = set()
    target_ids = {
        btn["data-modal-target"] for btn in content.select("[data-modal-target]")
        if btn.get("data-modal-target")
    }
    for target in target_ids:
        modal = soup.find(attrs={"data-modal-id": target})
        if modal is None:
            continue
        for link in tu.extract_ticket_links(
                modal, modal.get_text(" ", strip=True)):
            key = (link["provider"], link["url"], link["code"])
            if key not in seen:
                seen.add(key)
                links.append(link)
    return links


# --------------------------------------------------------------------- class
class UdoArtistsScraper(BaseScraper):
    source_id = SOURCE_ID
    source_name = "UDO ARTISTS"
    rate_limit_s = 2.0
    #: all enrichment happens inside scrape() (every show is fetched every
    #: run — there are only ~12); there is no separate per-event detail pass.
    supports_detail = False

    def __init__(self, **kw):
        super().__init__(**kw)
        #: raw venue strings of legs we could not resolve (report only;
        #: extend venues.py to pick these up).
        self.skipped_venues: set[str] = set()

    # ------------------------------------------------------------- fetching
    def scrape(self) -> Iterable[Event]:
        html = self.fetch(SHOWS_URL)
        seen: set[str] = set()
        for show_url in self.parse(html):
            try:
                detail_html = self.fetch(show_url)
            except RuntimeError:
                continue        # one bad show page never kills the run
            for ev in parse_show(detail_html, show_url,
                                 skipped=self.skipped_venues):
                if ev.source_url in seen:
                    continue
                seen.add(ev.source_url)
                yield ev

    # -------------------------------------------------------------- parsing
    def parse(self, html: str, **context) -> list[str]:
        """Pure listing parse. Unlike other sources this yields show
        detail URLs, not Events — /shows carries no date/venue/price at
        all, so there is nothing Event-shaped to build without fetching
        each show's own page (done in scrape(); see parse_show())."""
        return parse_shows(html)
