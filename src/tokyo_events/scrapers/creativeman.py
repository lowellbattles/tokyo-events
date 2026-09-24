"""Scraper for CREATIVEMAN — https://www.creativeman.co.jp

CREATIVEMAN is a concert PROMOTER, not a venue: a single organizer that
books shows (strong international roster) into many different halls. So
this is a new source class — a promoter calendar — and it deviates
DELIBERATELY from the usual two-stage (listing + per-event detail) pattern:

  * LISTING: month pages at /event/?cmy=YYYY&cmm=M render a static
    WordPress calendar grid. Each day cell (td.p-calendar__cell, with the
    day number in .p-calendar__label) holds zero-or-more
    div.p-calendar__live blocks. Each block is one show: an <a> to the
    tour page, a .p-calendar__areaLabel--<pref> chip giving the prefecture
    at LISTING level, the artist text, and an optional status badge
    (SOLD OUT / 発売中 / 当日券あり …). We keep only KANTO prefecture
    chips (tokyo/kanagawa/yokohama/chiba/saitama).

  * TOUR PAGES are ONE PER TOUR, not per date: every venue/date leg is a
    separate <table> under the "TICKET INFORMATION" heading. Each table's
    header cell is "<pref-kanji> YYYY/M/D(weekday) <venue>" (+ a SOLD OUT
    label), followed by th/td rows: ゲスト・Support Act (guests),
    開場・開演 (OPEN/START), チケット (price tiers), プレイガイド (playguide
    links). Because a page is shared across many calendar dates, we fetch
    each distinct Kanto tour URL ONCE per run (per-run dict cache) and
    yield one Event PER KANTO LEG. There is no separate per-event page, so
    supports_detail = False (all enrichment happens inside scrape).

Politeness: tour-page fetches go through self.fetch (rate limiter applies)
and are capped at tour_fetch_cap per run, fetched in date order. When the
cap is hit, the remaining calendar rows are still yielded WITHOUT leg
detail (date + artist + prefecture only, title from the artist) so the
pipeline stages them and a later run enriches them — the deferred row's
source_url ("<tour>#<ISO date>") matches the leg's source_url on the next
run, so the upsert fills in venue/times/price rather than duplicating.

Venue curation: a leg's raw venue string is resolved with venues.resolve_
venue at scrape time; legs that don't resolve are SKIPPED (the prefecture
filter already ran at listing level, so this second filter catches
un-curated Kanto halls). Skipped Kanto venue strings are collected on the
instance (self.skipped_venues) for the integrator to add to venues.py.
The RAW venue string is stored on kept Events — canonical resolution
happens again at export.

Microsites: flagship tours (RADIOHEAD 2027) link from the calendar to a
dedicated off-site page with no leg tables. Those rows keep their
calendar dates and take the venue from the single curated venue named in
the page's title/headings/img alt text; anything else is reported in
skipped_venues as "[no leg table] ..." instead of vanishing.

/artist/ pages (2026-09-24): a newer headliner-tour template at
/artist/YYYY/MMslug/ (a growing minority — 6 tours as of 2026-09) also has
no leg <table>s, but unlike a microsite it DOES carry full leg detail —
just in its own div.info-det/h3 markup instead of parse_tour's tables. When
parse_tour finds no legs, parse_artist_page() is tried first (full
times/prices/ticket_links/support-act, exactly like a table leg) and only
falls through to the microsite heuristic when that finds nothing either.
See parse_artist_page's own docstring for the template's shape and its one
real landmine (leftover cross-artist VIP-upsell markup).

Parsers key off URL/text conventions (the /event/ slug, the YYYY/M/D leg
header, the Japanese row labels) rather than CSS class names, so a
structural break yields zero events (loud), never silent garbage.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from typing import Iterable, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString

from ..models import Category, Event
from ..venues import display_of, resolve_venue, venues_named_in
from .base import BaseScraper, FetchError, NotFoundError
from . import textutils as tu

BASE = "https://www.creativeman.co.jp"
EVENT_BASE = f"{BASE}/event/"

#: areaLabel--<suffix> classes we keep at listing level (Kanto scope).
KANTO_CLASSES = {"tokyo", "kanagawa", "yokohama", "chiba", "saitama"}
#: leg-header prefecture kanji considered in-scope Kanto — used ONLY to
#: decide whether an unresolved venue is worth reporting (a national tour's
#: Osaka/Aichi legs also fail to resolve, but we don't want them in the
#: "un-curated Kanto hall" report).
KANTO_PREF_KANJI = {"東京", "神奈川", "千葉", "埼玉", "茨城", "栃木", "群馬", "横浜"}

AREA_RE = re.compile(r"p-calendar__areaLabel--(\w+)")
# Absolute YYYY/M/D inside a leg header (weekday paren follows, e.g.
# "東京 2026/7/15(水) Zepp Shinjuku" or "... 2026/7/19 (日) ...").
LEG_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_WEEKDAY_PAREN_RE = re.compile(r"^\s*[（(][^）)]*[)）]\s*")
# Two-night legs cram a second day into one header ("2026/12/11(金)・12(日)
# 東京ドーム") — strip the "・12(日)" remnant so the venue resolves. The
# second night itself is not (yet) emitted as its own event; before this
# fix the WHOLE leg was dropped as an unresolvable venue string.
_EXTRA_DAY_RE = re.compile(r"^[・･]\s*\d{1,2}\s*(?:[（(][^）)]*[)）])?\s*")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


# ---------------------------------------------------------------- tour parse
def parse_tour(html: str, tour_url: str | None = None,
               **context) -> dict:
    """Pure parse of a tour page into {title, artist, legs}. No network.

    `legs` is a list of dicts: pref (kanji), date (ISO), venue (raw string),
    open_time, start_time, price_text, price_min, is_free, ticket_links,
    guests, sold_out.  A leg is any <table> whose header cell carries an
    absolute YYYY/M/D — that keys off the site's text convention, not a CSS
    class, so a structure change yields zero legs (loud).
    """
    soup = BeautifulSoup(html, "lxml")

    # Tour/event title (the headline on the page) and the plain artist name.
    h1 = soup.select_one("h1.p-jumbotron__title") or soup.find("h1")
    page_title = _clean(h1.get_text(" ", strip=True)) if h1 else None
    artist = None
    if soup.title:
        # "<artist> - CREATIVEMAN PRODUCTIONS"
        artist = re.split(r"\s*[-|｜–]\s*CREATIVEMAN",
                          soup.title.get_text(strip=True))[0].strip() or None

    legs: list[dict] = []
    for table in soup.find_all("table"):
        hdr = None
        for th in table.find_all("th"):
            if LEG_DATE_RE.search(th.get_text(" ", strip=True)):
                hdr = th
                break
        if hdr is None:
            continue
        leg = _parse_leg(table, hdr)
        if leg:
            legs.append(leg)
    return {"title": page_title, "artist": artist, "legs": legs}


def _parse_leg(table, hdr) -> dict | None:
    # Clean leg header: the u-text-large span carries it without the SOLD OUT
    # label; fall back to the whole cell.
    span = hdr.select_one("span.u-text-large")
    header_text = _clean((span or hdr).get_text(" ", strip=True))
    m = LEG_DATE_RE.search(header_text)
    if not m:
        return None
    try:
        date = dt.date(int(m.group(1)), int(m.group(2)),
                       int(m.group(3))).isoformat()
    except ValueError:
        return None
    pref = header_text[:m.start()].strip()
    venue = _clean(_WEEKDAY_PAREN_RE.sub("", header_text[m.end():]))
    venue = _clean(_EXTRA_DAY_RE.sub("", venue))
    if not venue:
        return None

    sold_out = bool(hdr.select_one(".event-label__soldout")) or \
        bool(tu.SOLD_OUT_RE.search(hdr.get_text(" ", strip=True)))

    open_time = start_time = None
    price_text = price_min = is_free = None
    ticket_links: list[dict] = []
    guests: list[str] = []
    general_on_sale = None
    windows: list[dict] = []
    for tr in table.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if not (th and td):
            continue                       # header row (colspan) or spacer
        label = _clean(th.get_text(" ", strip=True))
        cell_text = td.get_text(" ", strip=True)
        if "開場" in label or "開演" in label:
            open_time, start_time = tu.parse_times(cell_text)
        elif label == "チケット":          # price row only (not 発売日 / 先行)
            price_text, price_min, is_free = tu.parse_prices(
                tu.strip_drink_charges(cell_text))
        elif "プレイガイド" in label:
            ticket_links = tu.extract_ticket_links(td, cell_text)
        elif "ゲスト" in label or "support act" in label.lower():
            guests = [g.strip() for g in re.split(r"[/／、,]", cell_text)
                      if g.strip()]
        elif "発売日" in label:            # チケット発売日: 6/3(水)10:00am～
            general_on_sale = _parse_general_sale(cell_text)
        elif "先行" in label:              # "<label> 期間：M/D(曜)HH:MM～…" ×N
            prev = 0
            for m in _SALE_WINDOW_RE.finditer(cell_text):
                name = re.sub(r"期間\s*[:：]?\s*$", "",
                              cell_text[prev:m.start()]).strip()
                prev = m.end()
                mo1, da1, t1, mo2, da2, t2 = m.groups()
                windows.append({"label": _clean(name) or label,
                                "opens": _norm_md_time(mo1, da1, t1),
                                "closes": _norm_md_time(mo2, da2, t2)})

    return {
        "pref": pref, "date": date, "venue": venue,
        "open_time": open_time, "start_time": start_time,
        "price_text": price_text, "price_min": price_min, "is_free": is_free,
        "ticket_links": ticket_links, "guests": guests, "sold_out": sold_out,
        "sales": {"general_on_sale": general_on_sale, "windows": windows},
    }


# ------------------------------------------------------- /artist/ page parse
# Headliner tours (a growing minority — 6 as of 2026-09) now live on a NEWER
# per-artist template at /artist/YYYY/MMslug/ instead of the classic
# leg-<table> template parse_tour() reads. Structurally: one <h3> per leg
# inside div.info-det (section#info is the Japanese copy; section#info-en
# duplicates everything in English right after it — we deliberately scope to
# #info only, or every leg would be double-counted). The h3's own <br> is the
# hard split between the "pref YYYY. M.D(wd) [/M.D(wd) ...]" date run (one
# <span> per night — a second night is a bare day number, e.g. "25", when it
# shares the first night's month, or "/12.2" when the site restates it) and
# the trailing venue <span>. A leg missing that <br> boundary, or whose first
# text node doesn't match "<pref> YYYY.", is a structural break -> skipped
# (loud: parse_artist_page returns zero legs for a page that doesn't fit).
#
# Landmine (found on the weezer/artgarfunkel fixtures): the CMS leaves
# leftover EVANESCENCE upsell markup (div.vipticket, "Premium Upgrade" /
# "Tour Upgrade" ticket-lines with real eplus.jp/evanescence links) sitting
# unrendered (style="display:none") inside otherwise-unrelated artist pages
# — copy-paste residue from the template, not this artist's own VIP tier.
# _base_ticket_lines() and the ticket_links clone both exclude div.vipticket
# AND any ticket-line whose name reads as an addon (UPGRADE/EXPERIENCE/
# PREMIUM) so this residue can never leak into price_min or ticket_links.
PREF_YEAR_RE = re.compile(r"(.+?)\s*(\d{4})\.?")
#: leg header day-token: "12.1" / "/12.2" (continuation, same or new month)
#: or a bare day "25" (continuation, inherits the running month).
_ADDON_TICKET_RE = re.compile(r"upgrade|experience|premium", re.I)
#: "受付期間：4/13(月)12:00～4/19(日)23:59" (weekday paren optional, "・祝"
#: allowed inside it); both fullwidth and ASCII dash/tilde separators seen.
_SALE_WINDOW_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})\s*(?:[（(][^）)]*[)）])?\s*(\d{1,2}:\d{2})\s*"
    r"[〜～~]\s*(\d{1,2})/(\d{1,2})\s*(?:[（(][^）)]*[)）])?\s*(\d{1,2}:\d{2})")


def _node_text(node) -> str:
    """Text of one h3 child — comments (the site leaves plenty inline, e.g.
    '<!-- <span class="kaijo-ttl">...--> ') must never leak into the venue
    string."""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    return node.get_text(" ", strip=True)


def _norm_md_time(mo: str, da: str, time_s: str) -> str:
    return f"{int(mo):02d}-{int(da):02d} {time_s}"


def _parse_general_sale(text: str | None) -> str | None:
    """'一般発売日：7/18(土)10:00am〜' -> '07-18 10:00'; a date with no time
    ('一般発売日：9/5(土)', seen on the weezer fixture) -> '09-05'. No year
    printed on the page, so none is guessed (roadmap: this is a facts-only
    scrape — a wrong guessed year is worse than an absent one)."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if not m:
        return None
    mo, da = int(m.group(1)), int(m.group(2))
    tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.I)
    if not tm:
        return f"{mo:02d}-{da:02d}"
    hour, minute, ap = int(tm.group(1)), tm.group(2), (tm.group(3) or "").lower()
    if ap == "pm" and hour != 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    return f"{mo:02d}-{da:02d} {hour:02d}:{minute}"


def _sales_windows(leg_div) -> list[dict]:
    """Every {label, opens, closes} presale window on the leg (member
    presales, playguide pre-orders, closed-out early tiers alike) — walked
    as h4-label/p-window pairs in document order so <del>-wrapped (expired)
    entries are picked up exactly like live ones. VIP-upsell h4/p pairs are
    excluded (see the module docstring landmine note)."""
    windows: list[dict] = []
    label = None
    for tag in leg_div.find_all(["h4", "p"]):
        if tag.find_parent(class_="vipticket"):
            continue
        if tag.name == "h4":
            label = _clean(tag.get_text(" ", strip=True))
            continue
        if label is None:
            continue
        text = tag.get_text(" ", strip=True)
        if "受付期間" not in text:
            continue
        m = _SALE_WINDOW_RE.search(text)
        if m:
            mo1, da1, t1, mo2, da2, t2 = m.groups()
            windows.append({
                "label": label,
                "opens": _norm_md_time(mo1, da1, t1),
                "closes": _norm_md_time(mo2, da2, t2),
            })
        label = None
    return windows


def _event_sales(sales: dict | None, show_date: str) -> list[dict]:
    """A leg's parsed sale info ({general_on_sale, windows}, month-day
    strings as printed) -> Event.sales, years inferred from the show date,
    de-duplicated and ordered by opening moment."""
    if not sales:
        return []
    out: list[dict] = []
    for w in sales.get("windows") or []:
        e = tu.sale_window(w.get("label"), w.get("opens"), w.get("closes"),
                           show_date)
        if e and e not in out:
            out.append(e)
    gen = tu.sale_window("一般発売", sales.get("general_on_sale"), None,
                         show_date)
    if gen and gen not in out:
        out.append(gen)
    return sorted(out, key=lambda e: e["opens"])


def _primary_playguide_links(leg_div) -> list[dict]:
    """The leg's real "buy now" playguide links — anchors with class
    ippan-hatubai (the site's one consistent marker for an actionable
    playguide button), excluding div.vipticket residue and unfilled
    placeholder anchors (href="#").

    Deliberately NOT a whole-leg scan for any anchor whose href matches a
    known ticket domain: the presale-history accordion re-states the SAME
    playguide links inside class="no-link" ("受付終了"/"受付はこちら")
    anchors once a window closes, and on the a7x fixture one of those
    closed-window anchors happens to point at a stale
    eplus.jp/dreamtheater/ URL from an earlier co-headline announcement —
    a real link, but not this leg's ticket. Scoping to ippan-hatubai only
    (the button class, never used for a historical/status anchor) keeps
    that out without guessing at div nesting, which varies leg to leg."""
    anchors = [a for a in leg_div.select("a.ippan-hatubai")
              if not a.find_parent(class_="vipticket")
              and (a.get("href") or "").strip() not in ("", "#")]
    if not anchors:
        return []
    frag = BeautifulSoup("<div></div>", "lxml").div
    for a in anchors:
        frag.append(BeautifulSoup(str(a), "lxml").a)
    text = " ".join(a.get_text(" ", strip=True) for a in anchors)
    return tu.extract_ticket_links(frag, text)


def _base_ticket_lines(leg_div) -> list:
    """The leg's real admission price tiers: every div.ticket-line except
    ones inside div.vipticket or named like an addon (UPGRADE/EXPERIENCE/
    PREMIUM) that requires a base ticket already in hand — see the module
    docstring landmine note. Both price_min and sold_out are derived from
    this list only."""
    lines = []
    for tl in leg_div.find_all("div", class_="ticket-line"):
        if tl.find_parent(class_="vipticket"):
            continue
        name_el = tl.select_one(".tickets-name")
        name = name_el.get_text(" ", strip=True) if name_el else ""
        if _ADDON_TICKET_RE.search(name):
            continue
        lines.append(tl)
    return lines


def _parse_artist_leg(leg_div) -> list[dict]:
    """One div.info-det -> zero-or-more leg dicts (one per night — a
    two-night leg like '12.1(火)/12.2(水)' shares venue/times/prices).
    A structural break (no <br> splitting date-run from venue, or a header
    that doesn't start with '<pref> YYYY.') yields [] — loud, not a guess."""
    h3 = leg_div.find("h3")
    if h3 is None:
        return []
    br = h3.find("br")
    if br is None:
        return []
    pre_nodes, post_nodes, reached = [], [], False
    for child in h3.children:
        if child is br:
            reached = True
            continue
        (post_nodes if reached else pre_nodes).append(child)

    first_text = next((n for n in pre_nodes if isinstance(n, NavigableString)
                       and not isinstance(n, Comment)), None)
    if first_text is None:
        return []
    m = PREF_YEAR_RE.match(_clean(str(first_text)))
    if not m:
        return []
    pref, year = m.group(1), int(m.group(2))

    month = None
    day_tokens: list[tuple[int, int]] = []
    for n in pre_nodes:
        if getattr(n, "name", None) != "span":
            continue
        t = _clean(n.get_text(strip=True)).lstrip("/／")
        if not t:
            continue
        if "." in t:
            mo_s, da_s = t.split(".", 1)
            month = int(mo_s)
            day = int(da_s)
        elif "/" in t:
            mo_s, da_s = t.split("/", 1)
            month = int(mo_s)
            day = int(da_s)
        elif t.isdigit():
            if month is None:
                continue                   # no month seen yet -> unparsable
            day = int(t)
        else:
            continue
        day_tokens.append((month, day))
    if not day_tokens:
        return []

    venue = _clean("".join(_node_text(n) for n in post_nodes))
    if not venue:
        return []

    support = leg_div.select_one(".support-sct")
    guests: list[str] = []
    if support:
        gtext = re.sub(r"^\s*Support Act\s*[:：]\s*", "",
                       support.get_text(" ", strip=True))
        guests = [g.strip() for g in re.split(r"[/／、,]", gtext) if g.strip()]

    open_time = start_time = None
    open_start = leg_div.select_one(".open-start")
    if open_start:
        open_time, start_time = tu.parse_times(open_start.get_text(" ", strip=True))

    base_lines = _base_ticket_lines(leg_div)
    price_text = price_min = is_free = None
    if base_lines:
        text = " ".join(tl.get_text(" ", strip=True) for tl in base_lines)
        price_text, price_min, is_free = tu.parse_prices(tu.strip_drink_charges(text))
    sold_out = bool(base_lines) and all(
        "soldout" in (tl.get("class") or []) for tl in base_lines)

    ticket_links = _primary_playguide_links(leg_div)

    sale_p = next((p for p in leg_div.select("p.info-sale-date")
                  if not p.find_parent(class_="vipticket")), None)
    sales = {
        "general_on_sale": _parse_general_sale(
            sale_p.get_text(" ", strip=True) if sale_p else None),
        "windows": _sales_windows(leg_div),
    }

    legs = []
    for mo, da in day_tokens:
        try:
            date = dt.date(year, mo, da).isoformat()
        except ValueError:
            continue
        legs.append({
            "pref": pref, "date": date, "venue": venue,
            "open_time": open_time, "start_time": start_time,
            "price_text": price_text, "price_min": price_min, "is_free": is_free,
            "ticket_links": ticket_links, "guests": guests, "sold_out": sold_out,
            "sales": sales,
        })
    return legs


def parse_artist_page(html: str, tour_url: str | None = None,
                      **context) -> dict:
    """Pure parse of a /artist/YYYY/MMslug/ headliner page into the same
    {title, artist, legs} shape as parse_tour() — a fallback for tours that
    have migrated to this newer template (parse_tour finds no leg <table>s
    on them). JA legs only: section#info-en duplicates every leg in English
    right after section#info, and would double every event if not excluded.
    """
    soup = BeautifulSoup(html, "lxml")
    artist = None
    if soup.title:
        artist = soup.title.get_text(strip=True).split("|", 1)[0].strip() or None

    legs: list[dict] = []
    info = soup.find("section", id="info")
    if info is not None:
        for leg_div in info.select("div.info-det"):
            legs.extend(_parse_artist_leg(leg_div))
    # Title kept as the plain artist name, not the promo tagline after "|"
    # in <title> — rule 1 is facts only, and that tagline is marketing copy.
    return {"title": artist, "artist": artist, "legs": legs}


# --------------------------------------------------------------------- class
class CreativemanScraper(BaseScraper):
    source_id = "creativeman"
    source_name = "CREATIVEMAN"
    rate_limit_s = 2.0
    #: all enrichment happens inside scrape(); there is no per-event page.
    supports_detail = False

    #: 12 months: big arena/stadium shows are announced (and presold) up
    #: to a year out — at 3 months RADIOHEAD's Jun-2027 GMO Arena run
    #: (announced 2026-09-07) was invisible. The cap must cover the
    #: whole horizon (~100 Kanto tours as of 2026-09): tours past it are
    #: yielded venue-less, and export drops venue-less promoter rows.
    def __init__(self, months_ahead: int = 12, tour_fetch_cap: int = 110,
                 **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead
        self.tour_fetch_cap = tour_fetch_cap
        #: raw venue strings of Kanto legs we could not resolve (report only).
        self.skipped_venues: set[str] = set()

    # ------------------------------------------------------------- fetching
    def scrape(self) -> Iterable[Event]:
        first = tu.jst_today().replace(day=1)
        rows: list[Event] = []
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{EVENT_BASE}?cmy={m.year}&cmm={m.month}"
            try:
                html = self.fetch(url)
            except NotFoundError:
                if i == 0:
                    raise                  # the current month must be reachable
                break                      # far-future month not published yet
            rows.extend(self.parse(html, month=m))
        yield from self._process(rows, floor_date=first.isoformat())

    def _process(self, rows: list[Event],
                 floor_date: str | None = None) -> Iterator[Event]:
        """Group listing rows by their shared tour URL, fetch each distinct
        Kanto tour page once (in date order, up to tour_fetch_cap) and yield
        one Event per kept Kanto leg; rows beyond the cap are deferred. Split
        out from scrape() so the cap/grouping logic is unit-testable without
        depending on the wall clock.

        `floor_date` (ISO) drops enriched legs that fall before the listing
        window — a tour page lists ALL its legs, including ones in an already-
        passed month that our forward month-walk never showed. Deferred rows
        come straight from the listing, so they are always in-window."""
        # Group calendar rows by their (shared) tour URL, ordered by the
        # tour's earliest Kanto date so the fetch budget covers the soonest
        # shows first.
        tours: dict[str, list[Event]] = {}
        for r in rows:
            tours.setdefault(r.source_url, []).append(r)
        ordered = sorted(
            tours.items(),
            key=lambda kv: min(e.start_date for e in kv[1] if e.start_date))

        cache: dict[str, str] = {}
        fetched = 0
        for tour_url, tour_rows in ordered:
            badge_sold: dict[str, bool] = {}
            for r in tour_rows:
                if r.start_date:
                    badge_sold[r.start_date] = (
                        badge_sold.get(r.start_date, False) or r.is_sold_out)
            artist_hint = tour_rows[0].title_ja

            if fetched < self.tour_fetch_cap:
                try:
                    if tour_url not in cache:
                        cache[tour_url] = self.fetch(tour_url)
                        fetched += 1
                    page = parse_tour(cache[tour_url], tour_url=tour_url)
                except FetchError:
                    yield from self._deferred(tour_rows)   # fetch failed
                    continue
                if not page["legs"]:
                    # Newer headliner template (/artist/YYYY/MMslug/) has no
                    # leg <table>s either — try its own parser before
                    # falling back to the microsite heuristic.
                    artist_page = parse_artist_page(cache[tour_url],
                                                    tour_url=tour_url)
                    if artist_page["legs"]:
                        yield from self._legs_to_events(
                            artist_page, tour_url, badge_sold, artist_hint,
                            floor_date)
                        continue
                    # flagship tours link to their own microsite, which has
                    # no leg tables (radiohead2027.jp) — never drop silently
                    yield from self._microsite_events(
                        cache[tour_url], tour_url, tour_rows, badge_sold)
                    continue
                yield from self._legs_to_events(
                    page, tour_url, badge_sold, artist_hint, floor_date)
            else:
                yield from self._deferred(tour_rows)

    def _legs_to_events(self, page: dict, tour_url: str,
                        badge_sold: dict[str, bool],
                        artist_hint: str | None,
                        floor_date: str | None = None) -> Iterator[Event]:
        # Resolve venues first: drop legs that don't map to a curated Kanto
        # venue, remembering Kanto-prefecture misses for the report. Past-
        # month legs (before the listing window) are dropped silently.
        kept: list[tuple[dict, str]] = []
        for leg in page["legs"]:
            if floor_date and leg["date"] < floor_date:
                continue
            key = resolve_venue(leg["venue"])
            if key is None:
                if leg["pref"] in KANTO_PREF_KANJI:
                    self.skipped_venues.add(leg["venue"])
                continue
            kept.append((leg, key))

        # Two Kanto legs on the same date need distinct source_urls; the
        # common one-leg-per-date case keeps the bare "#<date>" so a
        # cap-deferred row from an earlier run resolves to the same event.
        date_counts = Counter(leg["date"] for leg, _ in kept)
        artist = page["artist"] or artist_hint
        title = page["title"] or artist or artist_hint

        for leg, key in kept:
            date = leg["date"]
            frag = f"#{date}-{key}" if date_counts[date] > 1 else f"#{date}"
            lineup: list[str] = []
            if artist:
                lineup.append(artist)
            for g in leg["guests"]:
                if g and g not in lineup:
                    lineup.append(g)
            nonmusic = tu.is_nonmusic(title or "") or tu.is_nonmusic(artist or "")
            yield Event(
                source=self.source_id,
                source_url=tour_url + frag,
                title_ja=title,
                category=Category.OTHER if nonmusic else Category.MUSIC,
                start_date=date,
                open_time=leg["open_time"], start_time=leg["start_time"],
                venue_name=leg["venue"],          # RAW; canonicalized at export
                price_text=leg["price_text"], price_min=leg["price_min"],
                is_free=leg["is_free"],
                is_sold_out=leg["sold_out"] or badge_sold.get(date, False),
                ticket_links=leg["ticket_links"],
                sales=_event_sales(leg.get("sales"), date),
                lineup=lineup,
            )

    def _microsite_events(self, html: str, tour_url: str,
                          tour_rows: list[Event],
                          badge_sold: dict[str, bool]) -> Iterator[Event]:
        """Tour page without leg tables — typically a dedicated microsite
        whose dates/venue live in images. Dates come from our own Kanto
        calendar rows (authoritative); the venue is taken only when
        exactly ONE curated venue is named in the page's title, headings
        or image alt text. Otherwise the rows go out venue-less (export
        skips them) and the tour is reported for curation."""
        soup = BeautifulSoup(html, "lxml")
        bits = [soup.title.get_text(" ", strip=True) if soup.title else ""]
        bits += [h.get_text(" ", strip=True)
                 for h in soup.find_all(["h1", "h2", "h3"])]
        bits += [img.get("alt") or "" for img in soup.find_all("img")]
        keys = venues_named_in(" | ".join(bits))
        artist = tour_rows[0].title_ja
        if len(keys) != 1:
            self.skipped_venues.add(
                f"[no leg table] {artist} — {tour_url} "
                f"(venues named: {', '.join(sorted(keys)) or 'none'})")
            yield from self._deferred(tour_rows)
            return
        venue = display_of(keys.pop())
        seen: set[str] = set()
        for r in tour_rows:
            if not r.start_date or r.start_date in seen:
                continue
            seen.add(r.start_date)
            yield Event(
                source=self.source_id,
                source_url=f"{tour_url}#{r.start_date}",
                title_ja=artist,
                category=r.category,
                start_date=r.start_date,
                venue_name=venue,                 # canonical display name
                is_sold_out=badge_sold.get(r.start_date, False),
                lineup=[artist] if artist else [],
            )

    @staticmethod
    def _deferred(tour_rows: list[Event]) -> Iterator[Event]:
        """Yield cap-deferred calendar rows as minimal Events (date + artist
        + sold-out badge, no venue). A later run enriches them once the tour
        page fits the fetch budget."""
        seen: set[str] = set()
        for r in tour_rows:
            if not r.start_date:
                continue
            r.source_url = f"{r.source_url}#{r.start_date}"
            if r.source_url in seen:
                continue
            seen.add(r.source_url)
            yield r

    # -------------------------------------------------------- listing parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure calendar parse -> minimal Events, ONE per Kanto calendar row.

        Each carries only what scrape() needs to group and enrich: the tour
        URL (source_url), the date, the artist (title_ja) and the listing
        sold-out badge. venue_name stays None until a leg is parsed. Non-Kanto
        rows (osaka/aichi/…) are dropped here. Dates need the `month` context
        (the cell shows only a day number); without it, no rows are emitted.
        """
        soup = BeautifulSoup(html, "lxml")
        events: list[Event] = []
        for blk in soup.select(".p-calendar__live"):
            a = blk.find("a", href=True)
            if not a:
                continue
            area = blk.find(class_="p-calendar__areaLabel")
            pref_class = None
            if area:
                for c in area.get("class", []):
                    mm = AREA_RE.match(c)
                    if mm:
                        pref_class = mm.group(1)
                        break
            if pref_class not in KANTO_CLASSES:
                continue

            date = self._cell_date(blk, month)
            if not date:
                continue

            area_text = area.get_text(strip=True) if area else ""
            artist = a.get_text(" ", strip=True)
            if area_text and artist.startswith(area_text):
                artist = artist[len(area_text):]
            artist = _clean(artist)
            if not artist:
                continue

            badge_divs = blk.find_all("div", recursive=False)
            badge = badge_divs[0].get_text(" ", strip=True) if badge_divs else ""

            events.append(Event(
                source=self.source_id,
                source_url=urljoin(EVENT_BASE, a["href"]),
                title_ja=artist,
                category=(Category.OTHER if tu.is_nonmusic(artist)
                          else Category.MUSIC),
                start_date=date,
                is_sold_out=bool(tu.SOLD_OUT_RE.search(badge)),
                venue_name=None,
            ))
        return events

    @staticmethod
    def _cell_date(blk, month: dt.date | None) -> str | None:
        if month is None:
            return None
        cell = blk.find_parent("td")
        label = cell.find(class_="p-calendar__label") if cell else None
        if label is None:
            return None
        txt = label.get_text(strip=True)
        if not txt.isdigit():
            return None
        try:
            return dt.date(month.year, month.month, int(txt)).isoformat()
        except ValueError:
            return None
