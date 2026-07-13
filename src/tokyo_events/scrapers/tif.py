"""Scraper for Tokyo International Forum — https://www.t-i-forum.co.jp

東京国際フォーラム is one building with EIGHT halls (A, B7, B5, C, D7, D5,
D1, E) plus meeting rooms, a lobby gallery, a lounge and a ground plaza —
all sharing ONE event calendar. There is no per-hall listing URL or filter
parameter. We only want Hall A concerts (ホールA, 5,012 seats — the
complex's main concert hall), so the scraper is a funnel:

  1. LISTING (cheap, every run): month pages at
     /visitors/event/?year=YYYY&month=M render a per-date list. Each date
     block holds one-or-more event stubs carrying ONLY a title and a link
     to /visitors/event/detail.html?id=<ID>. No hall, no times, no price
     on the listing. The one classifier the site itself provides here is an
     audience tag (一般 = public / 関係者 = industry-only / 一般&関係者), so
     we drop stubs whose ONLY audience is 関係者 (not open to the public)
     and EMIT EVERY remaining public event. Provisional category is MUSIC
     unless tu.is_nonmusic flags the title as clearly non-music; the real
     decision waits for the hall. venue_name stays the whole complex until
     the detail pass resolves the hall.

  2. DETAIL (custom parse_detail, capped + drained by the pipeline): the
     detail page carries clean Japanese headings — 開催日時 (date range +
     開場/開演 times), 料金 (¥/円 tiers) and 会場 (a link to the hall's
     /visitors/facilities/<code>/ page). The 会場 link is the venue's OWN
     signal for where the event runs, so it is the definitive classifier:
     category stays MUSIC ONLY when the hall is ホールA (/facilities/a/) and
     the title is not tu.is_nonmusic; otherwise category becomes OTHER and
     venue_name records the ACTUAL hall (e.g. "東京国際フォーラム ホールC").
     So a corporate conference held in Hall A ends up OTHER, and a concert
     in Hall C ends up OTHER with its real hall — only genuine Hall A
     concerts survive as MUSIC.

Deliberately precision-first, matching textutils' stance: we do NOT keyword-
match titles for "concert-ness" (that would hide oddly-titled real concerts
like a brass-band or cinema-concert show). The hall + tu.is_nonmusic decide;
the rare non-concert booked into Hall A (an award show / big lecture the
is_nonmusic list misses) may slip through as MUSIC — acceptable, and caught
by the export-time genre pass / human review.

Because the hall is knowable only on the detail page, EVERY complex-wide
public event is detail-fetched to be classified. The pipeline's per-run
detail cap means the whole-complex backlog drains over several daily runs —
fine, since already-classified events stay put. Multi-day events repeat the
same detail id on every date they span, so listing groups stubs by id
(start_date = earliest, end_date = latest) and there is one event per id
(no #date fragment needed).

Parsers key off the detail.html?id= URL shape and the visible Japanese
heading text (開催日時 / 料金 / 会場) — not CSS class names — so a structural
break yields zero events (loud), never silent garbage.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

# Whole-complex metadata; every hall shares this building. venue_name is
# refined per-event by parse_detail once the hall is known.
VENUE = dict(
    venue_name="東京国際フォーラム",
    venue_area="Yurakucho",
    address="3-5-1 Marunouchi, Chiyoda-ku, Tokyo 100-0005",
    lat=35.6752, lng=139.7631,
)
HALL_A_NAME = "東京国際フォーラム ホールA"

# Detail-page anchor for an event, relative on the listing ("detail.html?id=X").
DETAIL_HREF_RE = re.compile(r"detail\.html\?id=([^\s\"'&#]+)")
# Facility (hall) code out of a /visitors/facilities/<code>/ link on the
# detail page's 会場 section.
FAC_RE = re.compile(r"/facilities/([a-z0-9]+)/")
# YYYY年M月D日 anywhere in a date cell or a 開催日時 block.
YMD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
# 開場 / 開演 times; run on NFKC-normalised text so ： and full-width digits
# have already collapsed to ASCII.
OPEN_JP_RE = re.compile(r"開場\s*:?\s*(\d{1,2}):(\d{2})")
START_JP_RE = re.compile(r"開演\s*:?\s*(\d{1,2}):(\d{2})")
# Price tiers: "25,300円" or "¥25,300" (NFKC folds ￥ -> ¥, full-width
# digits -> ASCII).
PRICE_RE = re.compile(r"([\d,]+)\s*円|¥\s*([\d,]+)")
FREE_RE = re.compile(r"入場無料|入場料無料|無料")

# Facility code -> official hall / room name (fills venue_name for the
# non-Hall-A events we record as OTHER).
FACILITY_NAMES = {
    "a": "ホールA", "b7": "ホールB7", "b5": "ホールB5", "c": "ホールC",
    "d7": "ホールD7", "d5": "ホールD5", "d1": "ホールD1", "e": "ホールE",
    "conference": "会議室", "lobby": "ロビーギャラリー",
    "lounge": "ラウンジ", "square": "地上広場",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("　", " ")).strip()


class TokyoIntlForumScraper(BaseScraper):
    source_id = "tokyo_intl_forum"
    source_name = "東京国際フォーラム"
    supports_detail = True

    BASE = "https://www.t-i-forum.co.jp"
    EVENT_BASE = "https://www.t-i-forum.co.jp/visitors/event/"

    def __init__(self, months_ahead: int = 4, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # -------------------------------------------------------------- fetching
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        merged: dict[str, Event] = {}
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.EVENT_BASE}?year={m.year}&month={m.month}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise          # the current month must be reachable
                break              # far-future month not published yet
            # A reachable future month with no event stubs at all means we've
            # walked past the published calendar — stop (like zepp.py).
            if "detail.html?id=" not in html and i > 0:
                break
            for ev in self.parse(html, page_url=url):
                _merge(merged, ev)
        yield from merged.values()

    # ---------------------------------------------------------- listing parse
    def parse(self, html: str, page_url: str | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse. Anchors to the detail.html?id= URL shape and
        reads the enclosing date cell + audience tag; class names are only a
        convenience. Groups multi-day stubs (same id) into one event.

        `today` is accepted for interface parity but unused — listing dates
        carry an explicit year, so parsing is deterministic without it.
        """
        page_url = page_url or self.EVENT_BASE
        soup = BeautifulSoup(html, "lxml")

        group = soup.find("ul", class_="p-eventTop-newsGroup")
        if group is None:
            return []              # structure changed -> loud (found == 0)

        # id -> aggregation state (url, title, set of ISO dates)
        agg: dict[str, dict] = {}
        for a in group.find_all("a", href=True):
            m = DETAIL_HREF_RE.search(a["href"])
            if not m:
                continue
            ev_id = m.group(1)

            # Audience: skip stubs that are ONLY for 関係者 (industry, not open
            # to the public). Prefer the machine data-sort-category on the
            # enclosing event <div>, fall back to the visible c-tag text.
            if self._industry_only(a):
                continue

            date = self._stub_date(a)
            if not date:
                continue           # can't place it in time -> skip

            title = _clean(a.get_text(" ", strip=True))
            if not title:
                continue

            state = agg.get(ev_id)
            if state is None:
                agg[ev_id] = {
                    "url": urljoin(page_url, a["href"]),
                    "title": title,
                    "dates": {date},
                }
            else:
                state["dates"].add(date)
                if len(title) > len(state["title"]):
                    state["title"] = title

        events: list[Event] = []
        for state in agg.values():
            dates = sorted(state["dates"])
            start, end = dates[0], dates[-1]
            title = state["title"]
            events.append(Event(
                source=self.source_id, source_url=state["url"],
                title_ja=title,
                # Provisional; the Hall-A gate in parse_detail is definitive.
                category=(Category.OTHER if tu.is_nonmusic(title)
                          else Category.MUSIC),
                start_date=start,
                end_date=end if end != start else None,
                **VENUE,
            ))
        return events

    def _industry_only(self, a) -> bool:
        """True when the stub's ONLY audience is 関係者 (staff/industry)."""
        block = a.find_parent(attrs={"data-sort-category": True})
        if block is not None:
            cat = (block.get("data-sort-category") or "").strip()
            # The event-level <div> carries a single value (関係者 / 一般 /
            # 両方); the enclosing date <li> carries a comma list. Only a bare
            # 関係者 means not-public; a mixed list means keep.
            if cat == "関係者":
                return True
            if cat in ("一般", "両方") or "," in cat:
                return False
        # Fallback: read the visible audience tag text near the anchor.
        scope = a.find_parent("div") or a.parent
        if scope is not None:
            tag = scope.find("span", class_="c-tag")
            if tag is not None:
                return _clean(tag.get_text()) == "関係者"
        return False

    def _stub_date(self, a) -> str | None:
        """ISO date for a listing stub, from its enclosing per-date cell
        (<dl> with a <dt> like '2026年07月03日（金）' / aria-label)."""
        dl = a.find_parent("dl")
        cell = dl.find("dt") if dl is not None else None
        if cell is None:
            return None
        text = f"{cell.get('aria-label', '')} {cell.get_text(' ', strip=True)}"
        m = YMD_RE.search(text)
        if not m:
            return None
        try:
            return dt.date(int(m.group(1)), int(m.group(2)),
                           int(m.group(3))).isoformat()
        except ValueError:
            return None

    # ----------------------------------------------------------- detail parse
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Resolve the hall (the whole point of this source) plus the
        authoritative date range, 開場/開演 times, and 料金 prices. Keeps
        MUSIC only for genuine Hall A concerts; anything else becomes OTHER
        with the real hall recorded in venue_name."""
        soup = BeautifulSoup(html, "lxml")
        main = soup.find("main") or soup   # scope out the nav's facility links

        # Fuller, cleaner title from the article heading.
        h1 = main.find("h1", class_="p-article-header") or main.find("h1")
        if h1:
            t = _clean(h1.get_text(" ", strip=True))
            if t:
                ev.title_ja = t

        sections = self._sections(main)

        # 開催日時 -> authoritative date range + 開場/開演 times.
        term_text, _ = sections.get("開催日時", ("", []))
        term_norm = unicodedata.normalize("NFKC", term_text)
        iso = []
        for y, mo, d in YMD_RE.findall(term_norm):
            try:
                iso.append(dt.date(int(y), int(mo), int(d)).isoformat())
            except ValueError:
                pass
        if iso:
            ev.start_date = iso[0]
            ev.end_date = iso[-1] if iso[-1] != iso[0] else None
        om = OPEN_JP_RE.search(term_norm)
        sm = START_JP_RE.search(term_norm)
        if om and not ev.open_time:
            ev.open_time = f"{int(om.group(1)):02d}:{om.group(2)}"
        if sm and not ev.start_time:
            ev.start_time = f"{int(sm.group(1)):02d}:{sm.group(2)}"

        # 料金 -> price tiers (¥ or 円). Cut at the first ※ note so trailing
        # eligibility text and ticket/FC URLs (which follow it) are excluded
        # and only price facts are stored. (Prices lack the 円 suffix on ages
        # like "4歳" anyway, so this is belt-and-braces for a clean
        # price_text.)
        fee_text, _ = sections.get("料金", ("", []))
        if ev.price_min is None and fee_text:
            fee_norm = unicodedata.normalize("NFKC", fee_text)
            fee_zone = re.split(r"※", fee_norm, maxsplit=1)[0]
            yen = []
            for a_grp, b_grp in PRICE_RE.findall(fee_zone):
                raw = (a_grp or b_grp).replace(",", "")
                if raw.isdigit():
                    yen.append(int(raw))
            if yen:
                ev.price_min = min(yen)
                ev.is_free = ev.price_min == 0
                ev.price_text = _clean(fee_zone)[:300]
            elif FREE_RE.search(fee_norm):
                ev.price_min, ev.is_free = 0, True
                ev.price_text = _clean(fee_norm)[:300]

        # Ticket links + SOLD OUT across the whole article body.
        body_text = main.get_text(" ", strip=True)
        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(main, body_text)
        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(body_text):
            ev.is_sold_out = True

        # 会場 -> the definitive hall gate.
        _, venue_nodes = sections.get("会場", ("", []))
        hall_name, is_hall_a = self._resolve_hall(venue_nodes)
        if is_hall_a and not tu.is_nonmusic(ev.title_ja or ""):
            ev.category = Category.MUSIC
            ev.venue_name = HALL_A_NAME
        else:
            ev.category = Category.OTHER
            if hall_name:
                ev.venue_name = f"東京国際フォーラム {hall_name}"
        return ev

    def _sections(self, main) -> dict[str, tuple[str, list]]:
        """Map each detail heading (開催日時 / 料金 / 会場 / ...) to the text
        and nodes that follow it up to the next heading. Keys on the visible
        heading text, so it survives class churn."""
        out: dict[str, tuple[str, list]] = {}
        for h in main.find_all(["h2", "h3", "h4"]):
            label = _clean(h.get_text(" ", strip=True))
            if not label:
                continue
            nodes = []
            for sib in h.find_next_siblings():
                if getattr(sib, "name", None) in ("h2", "h3", "h4"):
                    break
                nodes.append(sib)
            text = " ".join(n.get_text(" ", strip=True) for n in nodes)
            out.setdefault(label, (text, nodes))
        return out

    def _resolve_hall(self, nodes) -> tuple[str | None, bool]:
        """Return (hall_name, is_hall_a) from the 会場 section nodes."""
        for node in nodes:
            anchors = node.find_all("a", href=True) \
                if hasattr(node, "find_all") else []
            if getattr(node, "name", None) == "a" and node.get("href"):
                anchors = [node, *anchors]
            for link in anchors:
                m = FAC_RE.search(link["href"])
                if m:
                    code = m.group(1).lower()
                    name = FACILITY_NAMES.get(code) or _clean(
                        link.get_text(" ", strip=True)) or None
                    return name, code == "a"
        # No facility link — fall back to hall names in the plain text.
        text = " ".join(
            n.get_text(" ", strip=True)
            for n in nodes if hasattr(n, "get_text"))
        for code, name in FACILITY_NAMES.items():
            if name in text:
                return name, code == "a"
        return None, False


def _merge(merged: dict[str, Event], ev: Event) -> None:
    """Combine same-URL events across month pages (an event straddling a
    month boundary appears on both), keeping the widest date range."""
    prev = merged.get(ev.source_url)
    if prev is None:
        merged[ev.source_url] = ev
        return
    lo = min(d for d in (prev.start_date, ev.start_date) if d)
    his = [d for d in (prev.end_date, ev.end_date,
                       prev.start_date, ev.start_date) if d]
    hi = max(his)
    prev.start_date = lo
    prev.end_date = hi if hi != lo else None
    if len(ev.title_ja or "") > len(prev.title_ja or ""):
        prev.title_ja = ev.title_ja
