"""Scraper for LINE CUBE SHIBUYA (渋谷公会堂) — https://linecubeshibuya.com

The renamed / rebuilt Shibuya Public Hall (渋谷公会堂, reopened 2019 with
LINE naming rights), a ~2,000-seat municipal hall. The public schedule is
fully static server-rendered WordPress HTML — no JS, no XHR/API needed.

Month pages live at /category/event/eventYYYYMM (e.g. .../event202607).
There is NO "bare" current-month URL; the scraper always builds the
explicit eventYYYYMM path and walks forward month by month. Prev/next
links exist on the page but are not needed — months are computed here.

Listing card (verbatim shape, one <article> per event day):
    <article class="fuwa fuwa-up">
      <a href="https://linecubeshibuya.com/event/17379" class="innArticle">
        <div class="topDate">... <li class="month">7</li>
                               <li class="day">1</li> ... </div>
        <h3 class="iventTtl">フルタの方程式LIVE！…</h3>
        <p class="subTtl"><span>第１部：古田敦也/…</span></p>
        <div class="wrapperDate">
          <p class="date"><span>開場時間 12:30</span><span>開演時間 13:30</span></p>
          <p class="date"><span>開場時間 17:30</span><span>開演時間 18:30</span></p>
          <p class="date"></p>   (empty slots padded out to 5) …
        </div>
      </a>
    </article>

Key facts come straight from the listing: date = li.month + li.day with the
YEAR injected from the page's eventYYYYMM URL (the date block carries no
year — hence the ``month`` kwarg, pinned in tests). Title = h3.iventTtl;
performers = p.subTtl; open/start = the first (earliest) 開場時間 / 開演時間.
Multi-show days repeat <p class="date"> — this venue keeps ONE event per
day (one detail URL) and records the first performance's times.

Prices / ticket info are NOT in the listing (typical for a public hall);
they sit on the /event/{id} detail page in a <dl><dt>入場</dt><dd>…</dd>
block where amounts are written with a backslash-yen ("\\7,500", the JP
font glyph for ¥, a literal U+005C in the UTF-8 source) — so the ¥-keyed
generic base.parse_detail() can't read them, and parse_detail is overridden
here to normalise "\\N,NNN" -> "¥N,NNN" and read the 入場/料金 row.

Mixed-use hall: idol, enka, pop, hip-hop, classical, plus comedy/talk/
recital bookings. Category defaults to MUSIC; tu.is_nonmusic() tags the
clearly-non-concert rows (sports/ceremony/expo keywords) as OTHER. The
site publishes NO category label, so comedy/talk shows that miss the
shared non-music keyword set stay MUSIC (precision-first) — see caveats.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper

# Detail permalink: /event/<numeric post id>. Anchored so month-archive and
# nav links (/category/event/…) never match — this is the structural key.
DETAIL_HREF_RE = re.compile(r"/event/(\d+)/?$")

# Times: "開場時間 12:30" (listing) and "開場 16:30" (detail) both covered.
KAIJO_RE = re.compile(r"開場\s*(?:時間)?\s*[:：]?\s*(\d{1,2}:\d{2})")
KAIEN_RE = re.compile(r"開演\s*(?:時間)?\s*[:：]?\s*(\d{1,2}:\d{2})")

# Price amounts: ¥/￥, backslash-yen ("\\7,500"), or "N,NNN円".
_BACKSLASH_YEN_RE = re.compile(r"\\(?=\s*[\d０-９])")
YEN_ANY_RE = re.compile(r"[¥￥]\s*([\d,]+)|([\d,]+)\s*円")
FREE_RE = re.compile(r"入場無料|入場料無料|無料", re.I)

# Section labels inside a performer string: ＜メインアーティスト＞ / 【出演】 …
_SECTION_LABEL_RE = re.compile(r"[＜〈《【\[][^＞〉》】\]]{0,15}[＞〉》】\]]")
# Tokens that are noise, not a performer name.
_LINEUP_NOISE_RE = re.compile(r"^(?:他|ほか|出演|進行|MC|ゲスト|and\s*more)$", re.I)


class LineCubeShibuyaScraper(BaseScraper):
    source_id = "line_cube_shibuya"
    source_name = "LINE CUBE SHIBUYA"
    BASE = "https://linecubeshibuya.com"

    VENUE = dict(
        venue_name="LINE CUBE SHIBUYA（渋谷公会堂）",
        venue_area="Shibuya",
        address="1-1 Udagawacho, Shibuya-ku, Tokyo",
        lat=35.6627,
        lng=139.6975,
    )

    def __init__(self, months_ahead: int = 4, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ---------------------------------------------------------------- fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/category/event/event{m.year}{m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            # A real future month can legitimately be empty; stop after two
            # consecutive empties rather than walking the whole calendar.
            empty_streak = 0 if fresh else empty_streak + 1
            if i and empty_streak >= 2:
                break
            yield from fresh

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        # One <article> per event day; the detail link inside carries the id.
        for art in soup.find_all("article"):
            a = art.find("a", href=DETAIL_HREF_RE)
            if a is None:
                continue
            url = urljoin(self.BASE, a["href"].split("?")[0].rstrip("/"))
            ev = self._parse_article(art, url, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_article(self, art, url: str, month: dt.date | None,
                       today: dt.date | None) -> Event | None:
        # --- date: li.month + li.day, YEAR from the page (month kwarg) ---
        mo_el = art.find("li", class_="month")
        day_el = art.find("li", class_="day")
        if mo_el is None or day_el is None:
            return None
        try:
            art_month = int(mo_el.get_text(strip=True))
            art_day = int(day_el.get_text(strip=True))
        except ValueError:
            return None
        date = self._resolve_date(art_month, art_day, month, today)
        if not date:
            return None

        # --- title (h3.iventTtl) ---
        h3 = art.find("h3", class_="iventTtl") or art.find("h3")
        title = h3.get_text(" ", strip=True) if h3 else None
        if title:
            title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        # --- performers (p.subTtl) -> lineup ---
        sub = art.find("p", class_="subTtl")
        sub_text = re.sub(r"\s+", " ", sub.get_text(" ", strip=True)) if sub else ""
        lineup = _lineup_from_subtitle(sub_text)

        # --- times: first (earliest) 開場 / 開演 in document order ---
        wrap = art.find("div", class_="wrapperDate") or art
        block_text = wrap.get_text(" ", strip=True)
        km = KAIJO_RE.search(block_text)
        sm = KAIEN_RE.search(block_text)
        open_time = km.group(1) if km else None
        start_time = sm.group(1) if sm else None

        category = Category.MUSIC
        if tu.is_nonmusic(f"{title} {sub_text}"):
            category = Category.OTHER

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(art.get_text(" ", strip=True))),
            **self.VENUE,
        )

    def _resolve_date(self, art_month: int, art_day: int,
                      month: dt.date | None, today: dt.date | None) -> str | None:
        """Combine the card's month/day with the year from the page context.
        A monthly archive lists only its own month, but guard the Dec/Jan
        boundary in case a card ever spills across the year edge."""
        if month is not None:
            year = month.year
            if month.month == 12 and art_month == 1:
                year += 1
            elif month.month == 1 and art_month == 12:
                year -= 1
            try:
                return dt.date(year, art_month, art_day).isoformat()
            except ValueError:
                return None
        # No page context (defensive): infer a forward-looking year.
        return tu.infer_year(art_month, art_day, today)

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """LINE CUBE detail pages write prices with a backslash-yen glyph
        ("\\7,500" == ¥7,500) that the ¥-keyed generic parser misses, and
        times as 開場/開演. Read the 入場/料金 <dl> row here; ticket links and
        sold-out fall back to the shared conventions."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        if not (ev.open_time or ev.start_time):
            km = KAIJO_RE.search(text)
            sm = KAIEN_RE.search(text)
            ev.open_time = km.group(1) if km else ev.open_time
            ev.start_time = sm.group(1) if sm else ev.start_time

        if ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = _parse_price(soup, text)

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev


def _lineup_from_subtitle(sub_text: str) -> list[str]:
    """Best-effort performer list from p.subTtl. Splits on the strong JP
    separators ／(fullwidth slash) / 、／，(commas) — which in this venue's
    billing reliably separate distinct acts and never occur inside a single
    Latin act name (so "DISH//" survives intact). ASCII "/"-separated bills
    are left as one faithful token; the artist-crossref phase refines these.
    Section labels (＜メインアーティスト＞ 等) become separators; a lone 他/MC
    token is dropped."""
    if not sub_text:
        return []
    # Turn bracketed section labels into a separator so they act as boundaries.
    work = _SECTION_LABEL_RE.sub("、", sub_text)
    tokens = re.split(r"[／、，]", work)
    names: list[str] = []
    for tok in tokens:
        # Strip surrounding whitespace / middot only — NOT slashes, so an act
        # name that legitimately ends in "//" (e.g. "DISH//") survives.
        tok = tok.strip(" 　・").strip()
        if not tok or _LINEUP_NOISE_RE.match(tok) or len(tok) > 50:
            continue
        names.append(tok)
    if not names:
        cleaned = sub_text.strip()
        return [cleaned] if cleaned else []
    # dedupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:30]


def _parse_price(soup, page_text: str) -> tuple[str | None, int | None, bool | None]:
    """Read the 入場/料金/チケット <dl> row (backslash-yen or N,NNN円 tiers);
    fall back to a text window after the 入場/料金 marker. Returns
    (price_text, price_min, is_free)."""
    zone = None
    for dl in soup.find_all("dl"):
        dt_el = dl.find("dt")
        if dt_el and re.search(r"入\s*場|料金|チケット|TICKET|券種",
                               dt_el.get_text(strip=True), re.I):
            dd = dl.find("dd")
            if dd:
                zone = dd.get_text(" ", strip=True)
            break
    if zone is None:
        m = re.search(r"(?:入\s*場|料金|チケット)(.{0,300})", page_text, re.S)
        zone = m.group(1) if m else ""
        cut = re.search(r"主催|問い合わせ|お問い合わせ|スケジュール|一覧|※|■",
                        zone)
        if cut:
            zone = zone[:cut.start()]

    normalized = _BACKSLASH_YEN_RE.sub("¥", zone)
    yen: list[int] = []
    for a, b in YEN_ANY_RE.findall(normalized):
        raw = a or b
        try:
            yen.append(int(raw.replace(",", "")))
        except ValueError:
            continue
    price_text = re.sub(r"\s+", " ", normalized).strip()[:300] or None
    if yen:
        pmin = min(yen)
        return price_text, pmin, pmin == 0
    if FREE_RE.search(zone):
        return price_text, 0, True
    return price_text, None, None
