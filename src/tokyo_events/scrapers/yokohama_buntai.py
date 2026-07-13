"""Scraper for 横浜BUNTAI (Yokohama BUNTAI) — https://yokohama-buntai.jp

A ~5,000-cap general-purpose arena in Kannai / Naka-ku, Yokohama (opened
April 2024 on the former Yokohama Cultural Gymnasium site). Static
WordPress ("event" custom post type). The schedule archive at /event/ is
server-rendered HTML — plain requests retrieves complete per-event facts,
so there is NO detail pass (supports_detail = False, yokohama_arena /
kanadevia precedent). The WP REST endpoint (/wp-json/wp/v2/event) exists
but carries no schedule facts (empty content/acf), so it is unused.

Pagination is by MONTH via ?y=YYYY&m=M (M unpadded), NOT WordPress's
default /page/N/ (that path is a dead end — returns the same month). The
site's month nav is JS but reads/writes those query params, so scrape()
walks forward month-by-month and stops after two empty months.

Structure the parser keys off (TEXT labels, not CSS decoration — the
Japanese label rows are the reliable anchor):

    <div class="event">
      <div class="event-date">
        <div class="date" data-week="Sat">7.4</div>   one per listed day
        <div class="period">-</div>                    multi-day separator
        <div class="date" data-week="Sun">7.5</div>
      </div>
      <div class="event-flex"><div class="event-info">
        <div class="info-title">TITLE</div>            may hold legacy
                                                       <FONT>/<B>/<I> tags
        <div class="info">
          <div class="info-left">公演時間</div>        label
          <div class="info-right">開場：15時00分 ...</div>  value
        </div>
        ... 料金 / 出演者 / 公式サイト / お問合せ先 rows ...

The date blocks carry NO year (month + day only, e.g. "7.4"); the year
comes from the page itself (the ?y= tab / inline script), with
tu.infer_year as a fallback. Time labels vary across events — 開場/開演
("開場：15時00分" or "16:00開場" or "開場 15:00｜開演 17:00｜終演 20:00")
and occasional romanized OPEN/START — so _parse_times handles both label
positions and the kanji 時分 form. Prices are "N,NNN円" (円, NOT ¥) and
"無料"; tu.parse_prices only understands ¥, so this module parses 円 too.

The archive links no event to a detail page, so source_url is the month
archive + a "#YYYY-MM-DD-<title-hash>" fragment: stable across runs, unique
per event, and collision-proof for two shows on one day.

MIXED CALENDAR: BUNTAI interleaves concerts (K-pop, anime/seiyuu, J-rock)
with sports (B.League), religious conventions and radio field-days. Rows
clearly non-concert per tu.is_nonmusic (basketball, ice shows, ceremonies)
are kept as facts but tagged Category.OTHER. The site publishes NO per-row
type label, so — per house rule — no custom keyword list is invented; a few
venue-specific non-music rows (e.g. a religious 大会, a 大運動会) therefore
fall through as MUSIC and are left to review. Everything else is MUSIC.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

VENUE = dict(
    venue_name="横浜BUNTAI",
    venue_area="Kannai",
    address="2-7-1 Furocho, Naka-ku, Yokohama",
    # Approximate geocode (Kannai / Naka-ku); see module notes / caveats.
    lat=35.4453, lng=139.6396,
)

# "7.4" / "8.14"  ->  month, day  (no year in the block)
DAY_RE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})")
# Page's own displayed year: the active year tab, or the inline calendar seed.
ACTIVE_YEAR_RE = re.compile(r"(20\d{2})")
JS_DATE_YEAR_RE = re.compile(r"new\s+Date\(\s*(20\d{2})\s*,")
# "15時00分" -> "15:00"
KANJI_TIME_RE = re.compile(r"(\d{1,2})\s*時\s*(\d{2})\s*分")
HHMM_RE = re.compile(r"\d{1,2}:\d{2}")
# Time-field labels (kanji). START also covers 開始/開会 (e.g. a field-day
# 開会); END markers (終演/終了) are only used to bound a START value.
OPEN_LABEL = "開場"
START_LABEL = "開演|開始|開会"
END_LABEL = "終演|終了"
ANY_LABEL_RE = re.compile(r"開場|開演|開始|開会")
# amounts: "16,500円" (円 form, this venue) or "¥9,900" (symbol form, rarer)
AMOUNT_RE = re.compile(r"([\d,，]+)\s*円|[¥￥]\s*([\d,，]+)")
FREE_RE = re.compile(r"無料")
# Lineup noise: section headers ("- ARTIST -", "【ゲスト】"), announcements.
LINEUP_HEADER_RE = re.compile(
    r"^\s*[-‐–—【\[]*\s*(?:ARTIST|ATHLETE|GUEST|CAST|ゲスト|出演者)"
    r"\s*[-‐–—】\]:：]*\s*$", re.I)
LINEUP_NOISE_RE = re.compile(r"順次|追って|後日|発表|決定|ご案内|ご確認")


class YokohamaBuntaiScraper(BaseScraper):
    source_id = "yokohama_buntai"
    source_name = "Yokohama BUNTAI"
    BASE = "https://yokohama-buntai.jp"
    supports_detail = False        # the archive/month page is already complete

    def __init__(self, months_ahead: int = 8, **kw):
        super().__init__(**kw)
        self.months_ahead = months_ahead

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        first = dt.date.today().replace(day=1)
        seen: set[str] = set()
        empty_streak = 0
        for i in range(self.months_ahead):
            m = tu.add_months(first, i)
            url = f"{self.BASE}/event/?y={m.year}&m={m.month}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                if i == 0:
                    raise
                break
            fresh = [e for e in self.parse(html, year=m.year)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            empty_streak = 0 if fresh else empty_streak + 1
            if empty_streak >= 3:      # walked past the live window
                break
            yield from fresh

    # ------------------------------------------------- pure parse (html -> Events)
    def parse(self, html: str, year: int | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        page_year = year or self._page_year(soup, html)
        events: dict[str, Event] = {}
        for block in soup.select("div.event"):
            ev = self._parse_block(block, page_year, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _page_year(soup, html: str) -> int | None:
        """The displayed year, read from the page: the active year tab first,
        then the inline calendar seed (new Date(YYYY, ...))."""
        el = soup.select_one(".date-select .year-inner .active")
        if el:
            m = ACTIVE_YEAR_RE.search(el.get_text())
            if m:
                return int(m.group(1))
        m = JS_DATE_YEAR_RE.search(html)
        if m:
            return int(m.group(1))
        return None

    def _parse_block(self, block, page_year, today) -> Event | None:
        dates = self._block_dates(block, page_year, today)
        if not dates:
            return None                     # loud: no parseable date -> drop
        start_date = dates[0]
        end_date = dates[-1] if len(dates) > 1 and dates[-1] != dates[0] else None

        title_el = block.select_one(".event-info .info-title")
        title_ja = _clean(title_el.get_text(" ", strip=True)) if title_el else None
        if not title_ja:
            return None                     # loud: card with no title -> drop

        # Dispatch label rows by their Japanese <div class="info-left"> text.
        time_text = None
        price_el = cast_el = None
        ticket_url = None
        for info in block.select(".event-info .info"):
            left = info.select_one(".info-left")
            right = info.select_one(".info-right")
            if not left or right is None:
                continue
            label = left.get_text(strip=True)
            if "公演時間" in label:
                time_text = right.get_text("\n", strip=True)
            elif "料金" in label:
                price_el = right
            elif "出演者" in label:
                cast_el = right
            elif "公式サイト" in label and ticket_url is None:
                a = right.find("a", href=True)
                if a and a["href"].strip().startswith("http"):
                    ticket_url = a["href"].strip()

        open_time, start_time = _parse_times(time_text)
        price_text, price_min, is_free = _parse_price(price_el)
        lineup = _parse_lineup(cast_el)

        classify = " ".join(filter(None, [title_ja] + lineup))
        category = Category.OTHER if tu.is_nonmusic(classify) else Category.MUSIC

        source_url = f"{self.BASE}/event/#{start_date}-{_title_hash(title_ja)}"
        sold = bool((time_text and tu.SOLD_OUT_RE.search(time_text))
                    or (price_text and tu.SOLD_OUT_RE.search(price_text)))

        return Event(
            source=self.source_id, source_url=source_url,
            title_ja=title_ja, category=category, genres=[],
            start_date=start_date, end_date=end_date,
            open_time=open_time, start_time=start_time,
            price_text=price_text, price_min=price_min, is_free=is_free,
            is_sold_out=sold, ticket_url=ticket_url, lineup=lineup,
            **VENUE,
        )

    def _block_dates(self, block, page_year, today) -> list[str]:
        out: list[str] = []
        yr = page_year
        prev_month: int | None = None
        for node in block.select(".event-date .date"):
            m = DAY_RE.search(node.get_text(strip=True))
            if not m:
                continue
            mo, d = int(m.group(1)), int(m.group(2))
            if page_year is not None:
                if prev_month is not None and mo < prev_month:
                    yr = (yr or page_year) + 1      # month rollover (Dec -> Jan)
                try:
                    iso = dt.date(yr, mo, d).isoformat()
                except ValueError:
                    iso = None
            else:
                iso = tu.infer_year(mo, d, today)
            if iso:
                out.append(iso)
                prev_month = mo
        return out


# --------------------------------------------------------------------- utils
def _clean(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def _title_hash(title: str) -> str:
    """Stable short id from the normalized title text (tag churn is stripped
    before hashing, so <FONT> edits don't create a phantom new event)."""
    norm = unicodedata.normalize("NFKC", re.sub(r"\s+", "", title))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]


def _norm_time_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    return KANJI_TIME_RE.sub(r"\1:\2", t)


def _romaji_time(text: str, label: str) -> str | None:
    m = re.search(rf"{label}[^A-Za-z0-9]{{0,6}}(\d{{1,2}}:\d{{2}})", text, re.I)
    return m.group(1) if m else None


def _forward_time(text: str, label: str, stop: str | None) -> str | None:
    """label-first layout: first HH:MM after `label`, but before the next
    `stop` label so we don't borrow a neighbouring row's time."""
    lm = re.search(label, text)
    if not lm:
        return None
    rest = text[lm.end():]
    if stop:
        sm = re.search(stop, rest)
        if sm:
            rest = rest[:sm.start()]
    tm = HHMM_RE.search(rest)
    return tm.group(0) if tm else None


def _backward_time(text: str, label: str, prev: str | None) -> str | None:
    """time-first layout ("16:00開場"): last HH:MM before `label`, but after
    any preceding `prev` label so open/start don't collide."""
    lm = re.search(label, text)
    if not lm:
        return None
    head = text[:lm.start()]
    if prev:
        pm = None
        for pm in re.finditer(prev, head):
            pass
        if pm:
            head = head[pm.end():]
    times = HHMM_RE.findall(head)
    return times[-1] if times else None


def _parse_times(text: str | None) -> tuple[str | None, str | None]:
    """(open_time, start_time) from a 公演時間 value. Handles the kanji 時分
    form, romanized OPEN/START, and BOTH label orders — 開場：15:00 (label
    first) and 16:00開場 (time first) — detected once per field so a shared
    layout can't cross-assign the open time to start. 開始/開会 count as
    start; 終演/終了 only bound a start value."""
    if not text:
        return None, None
    t = _norm_time_text(text)
    open_time = _romaji_time(t, "OPEN")
    start_time = _romaji_time(t, "START")
    if open_time is not None and start_time is not None:
        return open_time, start_time

    lm = ANY_LABEL_RE.search(t)
    tm = HHMM_RE.search(t)
    time_first = bool(lm and tm and tm.start() < lm.start())
    if open_time is None:
        open_time = (_backward_time(t, OPEN_LABEL, None) if time_first
                     else _forward_time(t, OPEN_LABEL, START_LABEL))
    if start_time is None:
        start_time = (_backward_time(t, START_LABEL, OPEN_LABEL) if time_first
                      else _forward_time(t, START_LABEL, END_LABEL))
    return open_time, start_time


def _amount(s: str) -> int | None:
    m = AMOUNT_RE.search(s)
    if not m:
        return None
    digits = (m.group(1) or m.group(2)).replace(",", "").replace("，", "")
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_price(el) -> tuple[str | None, int | None, bool | None]:
    """(price_text, price_min, is_free). price_min = min of each line's
    leading amount, so an add-on component line can't undercut the floor.
    円 (this venue) and ¥ are both understood; '無料' -> free; a
    '公式サイトにてご確認ください' row (no amount) -> all None."""
    if el is None:
        return None, None, None
    lines = [ln.strip() for ln in el.get_text("\n").split("\n") if ln.strip()]
    joined = " ".join(lines)
    if FREE_RE.search(joined) and _amount(joined) is None:
        return "無料", 0, True
    leads = [a for a in (_amount(ln) for ln in lines) if a is not None]
    if not leads:
        return None, None, None
    return re.sub(r"\s+", " ", joined).strip()[:300], min(leads), False


def _parse_lineup(el) -> list[str]:
    """Best-effort performer list from a 出演者 value. Facts-only; artist
    normalization / member-splitting is a later pipeline phase, so we split
    ONLY on line breaks (splitting on 、 would shred group-member lists and
    URLs) and drop URLs, section headers, and 'to be announced' noise."""
    if el is None:
        return []
    out: list[str] = []
    for raw in el.get_text("\n").split("\n"):
        name = re.sub(r"\s+", " ", raw).strip(" 　・-‐–—")
        name = re.sub(r"^出演者\s*[:：]\s*", "", name).strip()
        if not name or name.startswith("http"):
            continue
        if LINEUP_HEADER_RE.match(name) or LINEUP_NOISE_RE.search(name):
            continue
        if name not in out:
            out.append(name)
        if len(out) >= 40:
            break
    return out
