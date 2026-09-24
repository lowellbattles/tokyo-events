"""Scraper for KAJIMOTO (カジモト) -- https://www.kajimotomusic.com

KAJIMOTO is one of Japan's two leading classical-music PROMOTERS (soloist
recitals, visiting orchestras, conductors) -- a promoter-class source
alongside sogo_tokyo/creativeman/smash_jpn/udo_artists, with its own shape:

  LISTING: /concerts/schedule/ is ONE static page holding the promoter's
  ENTIRE concert archive (357 shows spanning 2019-2027 as of 2026-09-24,
  newest-first) -- no month-walk needed, unlike every other promoter here
  (closer to tokyo_dome's "one static full-year page" pattern, just wider).
  Each show is one <section class="concert_info -cell">: a date header
  (year / M.D / weekday / start time), title (EN h3.concert_info_title_main
  + JA p.concert_info_title_sub), a "<pref>／<venue link>" line (the
  prefecture prefix is sometimes BLANK -- a handful of well-known Tokyo
  halls print no prefecture at all, e.g. bare "王子ホール"), and a "More
  Detail" link to the show's own page. We keep only rows dated >= today;
  the prefecture text is a REPORT-ONLY hint (like creativeman's
  KANTO_PREF_KANJI) -- venues.resolve_venue is the actual geography gate.

  DETAIL (one page per show, occasionally covering SEVERAL Kanto legs of
  one tour, e.g. LSO Japan Tour 2026 -> Kyoto + 2x Tokyo, or a 2-city
  "TWO DAYS WITH ARGERICH" pairing): under article#information, each leg is
  its own <section class="concert_info -col">, with:
    - p.concert_info_dates_date: two <span> -- a full JA date ("2026年9月
      26日（土）") and start/open times ("16:00 開演（15:15 開場）");
    - p.concert_info_venue_name ("<pref>／<a>venue</a>", pref sometimes
      blank, same convention as the listing);
    - ul.concert_info_price_items: one <li> per seat tier ("S席 ¥39,000");
      a tier whose OWN text names an age/status restriction (a "学生"
      student tier here, price li reads "学生￥5,000 (＊学生券は...)") must
      not win price_min -- see textutils.open_tier_min;
    - div.concert_info_buy > a: the primary buy link/status. Its text
      becomes "Thank you! SOLD OUT" when a leg sells out -- the per-leg
      sold-out signal (tu.SOLD_OUT_RE matches it directly);
    - div.concert_info_playguide: playguide anchors, INCLUDING the
      promoter's own e+ storefront at w1.onlineticket.jp/sf/kajimoto/...
      ("カジモト・イープラス" -- matched via
      textutils.TICKET_PROVIDERS["onlineticket.jp"], added 2026-09-24).
  A page-level "TICKETS" WordPress block (h2 + one loose <p> of <strong>
  label</strong><br><s>opens 〜 closes</s> lines) AFTER #information lists
  the presale/general-sale history for the WHOLE page -- applied to every
  leg (the page markets its legs as one announcement, not per-leg windows).

  #information also repeats every leg's price/buy/playguide block again
  under `.concert_info -modal` sections (a lightbox gallery) further down
  the page -- scoped OUT by only walking `article#information section
  .concert_info.-col`, or every leg would double.

  supports_detail = False: all enrichment happens inline in scrape(), one
  fetch per distinct show (~15-20 shows/run at the 2026-09-24 horizon).

  Lineup: PROGRAM blocks mix composer/piece names with the odd soloist
  aside ("（ヴァイオリン：HIMARI）") -- too unstructured to reliably split
  "artist" from "programme" without risking garbage (rule 1 is facts
  only). lineup is the show's own JA title instead, the same title-as-
  lineup fallback udo_artists/smash use when a site doesn't offer a
  cleaner split (a title like "伊藤恵 ～THE LEGENDS～" already names the
  artist).

Parsers key off the site's own BEM-ish content classes (concert_info_*)
and Japanese field conventions (開演/開場, ¥ tiers), not incidental
styling, so a structural break yields zero events (loud), never silent
garbage.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from ..venues import resolve_venue
from . import textutils as tu
from .base import BaseScraper, FetchError

BASE = "https://www.kajimotomusic.com"
SCHEDULE_URL = f"{BASE}/concerts/schedule/"

#: prefecture kanji considered in-scope Kanto for skipped_venues reporting
#: only (resolve_venue is the real gate) -- a blank prefecture is common
#: for well-known Tokyo halls the site doesn't bother prefixing, so it
#: counts as a Kanto hint too.
KANTO_PREF_KANJI = {"東京", "神奈川", "千葉", "埼玉", ""}

YMD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
START_OPEN_RE = re.compile(
    r"(\d{1,2}:\d{2})\s*開演[（(]\s*(\d{1,2}:\d{2})\s*開場[)）]")
YEN_LI_RE = re.compile(r"[¥￥]\s*[\d,，]+|[\d,，]{3,}\s*円")


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _split_pref_venue(text: str) -> tuple[str, str]:
    """'東京／サントリーホール' -> ('東京', 'サントリーホール'); a bare
    venue with no prefecture ('王子ホール') -> ('', '王子ホール')."""
    text = _clean(text)
    if "／" in text:
        pref, venue = text.split("／", 1)
        return pref.strip(), venue.strip()
    return "", text


# --------------------------------------------------------------- listing
def parse_schedule(html: str) -> list[dict]:
    """Pure parse of /concerts/schedule/ -> one dict per calendar row
    (every show the archive has ever listed, past and future -- callers
    filter to >= today). Each: {date (ISO), detail_url, title_en, title_ja,
    pref, venue_raw}. A row missing a parseable date is dropped (loud via
    an empty result if the whole page's structure breaks)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for sec in soup.select("section.concert_info.-cell"):
        rows.extend(_parse_row(sec))
    return rows


def _parse_row(sec) -> list[dict]:
    hdr = sec.select_one(".concert_info_dates")
    if hdr is None:
        return []
    date_p = hdr.find("p")
    if date_p is None:
        return []
    spans = date_p.find_all("span", recursive=False)
    em = date_p.find("em")
    if em is None or not spans:
        return []
    year_txt = spans[0].get_text(strip=True)
    md_txt = em.get_text(strip=True)
    m = re.match(r"(\d{1,2})\.(\d{1,2})", md_txt)
    if not (year_txt.isdigit() and m):
        return []
    try:
        date = dt.date(int(year_txt), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return []

    title_main = sec.select_one("h3.concert_info_title_main")
    title_sub = sec.select_one("p.concert_info_title_sub")
    title_en = _clean(title_main.get_text(" ", strip=True)) if title_main else None
    title_ja = _clean(title_sub.get_text(" ", strip=True)) if title_sub else None

    venue_p = sec.select_one("p.concert_info_venue_name")
    pref, venue_raw = _split_pref_venue(
        venue_p.get_text(" ", strip=True)) if venue_p else ("", "")

    detail_a = sec.select_one(".concert_info_btn a.btn")
    if detail_a is None or not detail_a.get("href"):
        return []
    detail_url = urljoin(SCHEDULE_URL, detail_a["href"])

    return [{
        "date": date, "detail_url": detail_url,
        "title_en": title_en, "title_ja": title_ja,
        "pref": pref, "venue_raw": venue_raw,
    }]


# ----------------------------------------------------------------- detail
def parse_concert_page(html: str) -> dict:
    """Pure parse of one /concerts/<slug>/ page -> {legs, sales}. `legs`
    is a list of per-date dicts (date, pref, venue_raw, open_time,
    start_time, price_text, price_min, is_free, ticket_links, sold_out);
    `sales` is the page-level presale/general-sale history (applied to
    every leg by the caller, year-inferred per leg's own date)."""
    if not html:
        return {"legs": [], "sales": []}
    soup = BeautifulSoup(html, "lxml")
    info = soup.find("article", id="information")
    legs: list[dict] = []
    if info is not None:
        for sec in info.select("section.concert_info.-col"):
            leg = _parse_leg(sec)
            if leg:
                legs.append(leg)
    sales = _parse_sales_block(soup)
    return {"legs": legs, "sales": sales}


def _parse_leg(sec) -> dict | None:
    date_p = sec.select_one("p.concert_info_dates_date")
    if date_p is None:
        return None
    date_spans = date_p.find_all("span")
    if not date_spans:
        return None
    m = YMD_RE.search(date_spans[0].get_text(strip=True))
    if not m:
        return None
    try:
        date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None
    times_text = date_spans[1].get_text(strip=True) if len(date_spans) > 1 else ""
    tm = START_OPEN_RE.search(times_text)
    start_time = tm.group(1) if tm else None
    open_time = tm.group(2) if tm else None

    venue_p = sec.select_one("p.concert_info_venue_name")
    if venue_p is None:
        return None
    pref, venue_raw = _split_pref_venue(venue_p.get_text(" ", strip=True))
    if not venue_raw:
        return None

    li_texts = [_clean(li.get_text(" ", strip=True))
                for li in sec.select("div.concert_info_price li")]
    entries = [(t, yen) for t in li_texts for yen in _yen_amounts(t)]
    restricted = tu.restricted_tier_names(sec.get_text(" ", strip=True))
    price_min = tu.open_tier_min(entries, restricted)
    price_text = "; ".join(
        t for t in li_texts if YEN_LI_RE.search(t) or len(t) <= 20)[:300] or None
    is_free = (price_min == 0) if price_min is not None else None

    buy_a = sec.select_one("div.concert_info_buy a")
    buy_text = buy_a.get_text(" ", strip=True) if buy_a else ""
    sold_out = bool(tu.SOLD_OUT_RE.search(buy_text))

    playguide = sec.select_one("div.concert_info_playguide")
    ticket_links = (tu.extract_ticket_links(
        playguide, playguide.get_text(" ", strip=True))
        if playguide is not None else [])

    return {
        "date": date, "pref": pref, "venue_raw": venue_raw,
        "open_time": open_time, "start_time": start_time,
        "price_text": price_text, "price_min": price_min, "is_free": is_free,
        "ticket_links": ticket_links, "sold_out": sold_out,
    }


def _yen_amounts(text: str) -> list[int]:
    out = []
    for m in re.finditer(r"[¥￥]\s*([\d,，]+)|([\d,，]{3,})\s*円", text):
        raw = m.group(1) or m.group(2)
        try:
            out.append(int(re.sub(r"[,，]", "", raw)))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------- sale windows
_SALES_DATE_TIME_RE = re.compile(
    r"(?:\d{4}年)?(\d{1,2})月(\d{1,2})日(?:[（(][^）)]*[)）])?\s*"
    r"(\d{1,2}):(\d{2})")
_SALES_STATUS_RE = re.compile(r"SOLD OUT|受付終了|Thank you", re.I)


def _parse_sales_block(soup) -> list[dict]:
    """Page-level ticket-sale history from the WordPress 'TICKETS' block
    (h2 + one loose <p>). Returns [{label, opens, closes}] as raw MM-DD
    HH:MM strings (no year -- textutils.sale_window infers it per leg).
    A page without this block (or a structural break in it) yields []."""
    h2 = next((h for h in soup.find_all("h2")
               if h.get_text(strip=True) == "TICKETS"), None)
    if h2 is None:
        return []
    p = h2.find_next_sibling("p")
    if p is None:
        return []
    lines = [l for l in p.get_text("\n", strip=True).split("\n") if l.strip()]

    entries: list[dict] = []
    label: str | None = None
    for line in lines:
        dates = _SALES_DATE_TIME_RE.findall(line)
        if dates:
            mo1, da1, h1, m1 = dates[0]
            opens = f"{int(mo1):02d}-{int(da1):02d} {h1}:{m1}"
            closes = None
            if len(dates) >= 2:
                mo2, da2, h2_, m2 = dates[1]
                closes = f"{int(mo2):02d}-{int(da2):02d} {h2_}:{m2}"
            entries.append({"label": label or "", "opens": opens,
                            "closes": closes})
            label = None
        elif _SALES_STATUS_RE.search(line):
            continue
        else:
            # a label can be split across text nodes by an inline tag
            # (e.g. "追加販売（<strong>関係者席開放</strong>）" -> 3 lines)
            # -- accumulate until the next date line consumes it.
            label = (label or "") + line
    return entries


def _event_sales(entries: list[dict], show_date: str) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        w = tu.sale_window(e["label"], e["opens"], e["closes"], show_date)
        if w and w not in out:
            out.append(w)
    return sorted(out, key=lambda e: e["opens"])


# --------------------------------------------------------------------- class
class KajimotoScraper(BaseScraper):
    source_id = "kajimoto"
    source_name = "KAJIMOTO"
    rate_limit_s = 2.0
    #: all enrichment happens inline in scrape(); there is no separate
    #: per-event detail pass -- the "detail" page IS what we fetch here.
    supports_detail = False

    def __init__(self, **kw):
        super().__init__(**kw)
        #: raw venue strings of plausibly-Kanto legs we could not resolve
        #: (report only; extend venues.py to pick these up).
        self.skipped_venues: set[str] = set()

    def scrape(self) -> Iterable[Event]:
        html = self.fetch(SCHEDULE_URL)
        rows = self.parse(html)
        floor = tu.jst_today().isoformat()
        upcoming = [r for r in rows if r["date"] >= floor]

        tours: dict[str, list[dict]] = {}
        for r in upcoming:
            tours.setdefault(r["detail_url"], []).append(r)
        ordered = sorted(tours.items(),
                         key=lambda kv: min(r["date"] for r in kv[1]))

        for detail_url, listing_rows in ordered:
            try:
                detail_html = self.fetch(detail_url)
            except FetchError:
                continue
            page = parse_concert_page(detail_html)
            yield from self._legs_to_events(page, detail_url, listing_rows, floor)

    def _legs_to_events(self, page: dict, detail_url: str,
                        listing_rows: list[dict],
                        floor: str) -> Iterator[Event]:
        title_ja = listing_rows[0]["title_ja"]
        title_en = listing_rows[0]["title_en"]
        title = title_ja or title_en

        kept: list[tuple[dict, str]] = []
        for leg in page["legs"]:
            if leg["date"] < floor:
                continue
            key = resolve_venue(leg["venue_raw"])
            if key is None:
                if leg["pref"] in KANTO_PREF_KANJI:
                    self.skipped_venues.add(leg["venue_raw"])
                continue
            kept.append((leg, key))

        date_counts: dict[str, int] = {}
        date_venue_counts: dict[tuple[str, str], int] = {}
        for leg, key in kept:
            date_counts[leg["date"]] = date_counts.get(leg["date"], 0) + 1
            dv = (leg["date"], key)
            date_venue_counts[dv] = date_venue_counts.get(dv, 0) + 1

        nonmusic = tu.is_nonmusic(title or "") or tu.is_nonmusic(title_en or "")
        for leg, key in kept:
            date = leg["date"]
            if date_venue_counts[(date, key)] > 1:
                # a same-day, same-venue repeat (matinee + evening) needs
                # the start time too, or both legs collide on one fragment
                frag = f"#{date}-{key}-{leg['start_time'] or 'na'}"
            elif date_counts[date] > 1:
                frag = f"#{date}-{key}"
            else:
                frag = f"#{date}"
            yield Event(
                source=self.source_id,
                source_url=detail_url + frag,
                title_ja=title_ja, title_en=title_en,
                category=Category.OTHER if nonmusic else Category.MUSIC,
                genres=["classical"],
                start_date=date,
                open_time=leg["open_time"], start_time=leg["start_time"],
                venue_name=leg["venue_raw"],       # RAW; canonicalized at export
                price_text=leg["price_text"], price_min=leg["price_min"],
                is_free=leg["is_free"],
                is_sold_out=leg["sold_out"],
                ticket_links=leg["ticket_links"],
                sales=_event_sales(page["sales"], date),
                lineup=[title] if title else [],
            )

    # -------------------------------------------------------- listing parse
    def parse(self, html: str, **context) -> list[dict]:
        """Pure listing parse. Unlike other sources this yields plain dicts
        (date/detail_url/title/pref/venue), not Events -- the archive page
        mixes past and future shows and carries no price/time data of its
        own; scrape() filters to upcoming rows and fetches each distinct
        show's own page for the rest (see module docstring)."""
        return parse_schedule(html)
