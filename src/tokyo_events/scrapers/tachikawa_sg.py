"""Scraper for TACHIKAWA STAGE GARDEN (立川ステージガーデン)
— https://www.t-sg.jp

A ~2,500-seat multi-purpose entertainment hall in the GREEN SPRINGS complex
in Tachikawa (opened 2020, run by TACHIHI HOLDINGS). The schedule is fully
static server-rendered HTML — no JS, no XHR/API needed.

NOTE the apex domain ``t-sg.jp`` refuses connections; the site only serves
on the ``www`` subdomain, so BASE is ``https://www.t-sg.jp``.

Month pages live at ``/events/index.php?y=YYYY&m=MM`` (bare ``/events/`` is
the current month, equivalent to y=&m= for this month). Each month page is a
calendar table with ONE ``<tr>`` per day of the month; non-booked days render
a "Reserved" block with no link. The scraper walks forward month by month
(the Zepp/LINE CUBE pattern) since no single page holds the whole schedule.

Listing row (verbatim shape, one <tr> per calendar day):
    <tr class="finished">
    <th class="en">07/05<br>SUN</th>
    <td>
      <div class="event-block hall" data-genre="ライブ">
        <a href="/events/2026/07/00001373.php" class="event-block-inner">
          <div class="flex-wrapper">
            <div class="pic-block"><img src="..." alt="ばってん少女隊…"></div>
            <div class="text-block">
              <h4 class="…"><span class="icm icon-arrow-right"></span> ばってん少女隊…</h4>
              <p>16:00開場／17:00開演</p>
              <p>Live Nation H.I.P.<br>TEL 03-3475-9999</p>  ← promoter, NOT lineup
            </div>
          </div>
        </a>
      </div>
    </td>
    </tr>

Facts taken from the listing: the detail URL (/events/YYYY/MM/NNNNNNNN.php,
the structural key), date = <th> MM/DD with the YEAR injected from the page
month (the block carries no year — hence the ``month`` kwarg, pinned in
tests), title = <h4> (img alt is the fallback), open/start = the earliest
"HH:MM開場" / "HH:MM開演" (numbered ①②… shows list earliest first). The
listing's trailing <p> is the promoter/contact line, so lineup is left to the
detail pass. The venue tags each block with data-genre="ライブ"/"ダンス"/
"企業説明会"/… — an event-TYPE label (not a music sub-genre), kept as a tag
and folded into the non-music check.

Prices are NOT in the listing; they sit on the /events/…/NNNNNNNN.php detail
page in a clean 概要 (outline) <table> keyed by JP <th> labels 公演日時 /
出演者 / 料金. Amounts are written "N,NNN円(税込)" (円 suffix, no ¥), which the
¥-keyed generic base.parse_detail() can't read, so parse_detail is overridden
to read the 料金 row (円 or ¥) and pull performers from 出演者.

Mixed-use hall: idol, J-rock, anime-song fests, dance competitions, plus the
occasional corporate job fair. Category defaults to MUSIC; tu.is_nonmusic()
(fed title + the site's own data-genre label) tags the clearly-non-concert
rows (the job fair's 企業説明会 matches 説明会) as OTHER. Ambiguous rows the
shared keyword set doesn't cover (e.g. a dance CHAMPIONSHIP tagged ダンス)
stay MUSIC, precision-first — see caveats.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper

# Detail permalink: /events/YYYY/MM/<zero-padded id>.php. Anchored on the
# URL shape so nav links (/events/, /about/, index.php?…) never match — this
# is the structural key; if the markup churns, no match -> 0 events (loud).
DETAIL_HREF_RE = re.compile(r"/events/(\d{4})/(\d{2})/\d+\.php")

# <th class="en">07/05<br>SUN</th> -> month / day (year comes from the page).
TH_DATE_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")

# Times: "16:00開場／17:00開演" (time BEFORE the JP marker). 開始 seen as a
# 開演 synonym on non-concert rows.
KAIJO_RE = re.compile(r"(\d{1,2}:\d{2})\s*開場")
KAIEN_RE = re.compile(r"(\d{1,2}:\d{2})\s*(?:開演|開始)")

# Price amounts on detail: "9,000円" (円 suffix) or "¥9,000".
YEN_ANY_RE = re.compile(r"[¥￥]\s*([\d,]+)|([\d,]+)\s*円")


class TachikawaStageGardenScraper(BaseScraper):
    source_id = "tachikawa_stage_garden"
    source_name = "TACHIKAWA STAGE GARDEN"
    BASE = "https://www.t-sg.jp"

    # address + lat/lng confirmed against the venue's /access/ page
    # (〒190-0014 東京都立川市緑町3-3 N1; Google Maps embed centre).
    VENUE = dict(
        venue_name="TACHIKAWA STAGE GARDEN（立川ステージガーデン）",
        venue_area="Tachikawa",
        address="3-3 Midori-cho, Tachikawa-shi, Tokyo (GREEN SPRINGS N1)",
        lat=35.704859,
        lng=139.410453,
    )

    def __init__(self, months_ahead: int = 3, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ---------------------------------------------------------------- fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            if i == 0:
                url = f"{self.BASE}/events/"
            else:
                url = f"{self.BASE}/events/index.php?y={m.year}&m={m.month:02d}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break            # far-future months eventually 404 / error
            for ev in self.parse(html, month=m):
                # A future month can legitimately be all-Reserved (0 events);
                # never stop early on that — only a fetch failure ends the walk.
                if ev.source_url not in seen:
                    seen.add(ev.source_url)
                    yield ev

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        """Pure listing parse: HTML string -> list[Event]. ``month`` pins the
        page's year (the row date carries none); ``today`` only matters in the
        defensive no-page-context path."""
        soup = BeautifulSoup(html, "lxml")
        rows: list[tuple[str, str, Event]] = []
        for a in soup.find_all("a", href=DETAIL_HREF_RE):
            parsed = self._parse_row(a, month, today)
            if parsed:
                rows.append(parsed)

        # A detail page normally covers one calendar day (this venue assigns a
        # distinct event id per performance), but guard the rare case where one
        # URL is listed on two dates: give each occurrence a #YYYY-MM-DD
        # fragment so dedupe keys stay unique (yokohama_arena precedent).
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

    def _parse_row(self, a, month: dt.date | None,
                   today: dt.date | None) -> tuple[str, str, Event] | None:
        url = urljoin(self.BASE, a["href"])

        # --- date: <th> MM/DD in this row, YEAR injected from the page ---
        tr = a.find_parent("tr")
        th = tr.find("th") if tr else None
        date = self._resolve_date(th, month, today)
        if not date:
            return None

        # --- title: <h4> (drops the leading icon span); img alt fallback ---
        title = None
        h4 = a.find("h4")
        if h4:
            title = re.sub(r"\s+", " ", h4.get_text(" ", strip=True)).strip()
        if not title:
            img = a.find("img", alt=True)
            if img:
                title = re.sub(r"\s+", " ", img["alt"]).strip()
        if not title:
            return None

        block_text = a.get_text(" ", strip=True)

        # --- times: earliest HH:MM開場 / HH:MM開演 (adjacency-anchored) ---
        km = KAIJO_RE.search(block_text)
        sm = KAIEN_RE.search(block_text)
        open_time = km.group(1) if km else None
        start_time = sm.group(1) if sm else None

        # --- the venue's own event-TYPE label (data-genre) ---
        genre = ""
        block_div = a.find_parent("div", class_="event-block")
        if block_div and block_div.has_attr("data-genre"):
            genre = (block_div["data-genre"] or "").strip()

        category = (Category.OTHER
                    if tu.is_nonmusic(f"{title} {genre}") else Category.MUSIC)

        ev = Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(block_text)),
            tags=[genre] if genre else [],
            **self.VENUE,
        )
        return (url, date, ev)

    def _resolve_date(self, th, month: dt.date | None,
                      today: dt.date | None) -> str | None:
        if th is None:
            return None
        m = TH_DATE_RE.search(th.get_text(" ", strip=True))
        if not m:
            return None
        mo, day = int(m.group(1)), int(m.group(2))
        if month is not None:
            year = month.year
            # A month archive lists only its own month; guard the Dec/Jan edge.
            if month.month == 12 and mo == 1:
                year += 1
            elif month.month == 1 and mo == 12:
                year -= 1
            try:
                return dt.date(year, mo, day).isoformat()
            except ValueError:
                return None
        return tu.infer_year(mo, day, today)

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Read the detail page's 概要 <table> (公演日時 / 出演者 / 料金).
        Prices are written "N,NNN円" (円 suffix) which the ¥-keyed generic
        parser misses; performers live in 出演者. Ticket links / sold-out
        fall back to the shared conventions (the outbound '公式サイト' link is
        usually the artist site, not a playguide, so it is not a ticket link).
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        rows = self._outline_rows(soup)

        if not (ev.open_time or ev.start_time):
            when = rows.get("公演日時", "")
            km = KAIJO_RE.search(when)
            sm = KAIEN_RE.search(when)
            ev.open_time = km.group(1) if km else ev.open_time
            ev.start_time = sm.group(1) if sm else ev.start_time

        if not ev.lineup:
            performers = rows.get("出演者", "")
            ev.lineup = _split_performers(performers)

        if ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = _parse_price(
                rows.get("料金", ""))

        if not ev.ticket_links:
            ev.ticket_links = tu.extract_ticket_links(soup, text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev

    @staticmethod
    def _outline_rows(soup) -> dict[str, str]:
        """Map the 概要 table's <th> label -> <td> text. Keyed off the JP
        labels, not CSS, so a table restyle can't silently swap fields."""
        out: dict[str, str] = {}
        for tr in soup.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if not th or not td:
                continue
            label = re.sub(r"\s+", "", th.get_text(strip=True))
            out.setdefault(label, td.get_text("\n", strip=True))
        return out


def _split_performers(text: str) -> list[str]:
    """Best-effort performer list from the 出演者 cell. Splits only on the
    strong JP separators ／、,・ and newlines (which reliably separate acts
    here) so a Latin act name keeps its internal '/'. Drops empties / obvious
    noise tokens."""
    if not text:
        return []
    tokens = re.split(r"[／、,・\n]+", text)
    names: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        tok = tok.strip(" 　").strip()
        if not tok or len(tok) > 60:
            continue
        if re.fullmatch(r"(?:他|ほか|and\s*more|ほか出演者)", tok, re.I):
            continue
        if tok not in seen:
            seen.add(tok)
            names.append(tok)
    return names[:30]


def _parse_price(text: str) -> tuple[str | None, int | None, bool | None]:
    """Parse the 料金 cell: "N,NNN円" and/or "¥N,NNN" tiers -> (price_text,
    price_min, is_free). Non-price numerals in the cell (e.g. 'U-22', '4枚',
    '22歳') carry no 円/¥ and are ignored."""
    if not text:
        return None, None, None
    yen: list[int] = []
    for a, b in YEN_ANY_RE.findall(text):
        raw = a or b
        try:
            yen.append(int(raw.replace(",", "")))
        except ValueError:
            continue
    price_text = re.sub(r"\s+", " ", text).strip()[:300] or None
    if yen:
        pmin = min(yen)
        return price_text, pmin, pmin == 0
    if re.search(r"無料|入場無料", text):
        return price_text, 0, True
    return price_text, None, None
