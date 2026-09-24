"""Scraper for JAPAN ARTS (ジャパン・アーツ) -- https://www.japanarts.co.jp

JAPAN ARTS is the other of Japan's two leading classical-music PROMOTERS
(soloist recitals, visiting orchestras) -- a promoter-class source
alongside kajimoto/sogo_tokyo/creativeman/smash_jpn/udo_artists, with its
own shape:

  LISTING: /concert lists every currently-marketed show as a WordPress
  archive, genre-filterable client-side but server-paginated at
  /concert/page/N/. Pagination here is NOT date-ordered (page 2 mixes
  dates already on page 1, dates already past `today`, and dates far past
  page 1's range) and out-of-range pages silently re-serve the last real
  page instead of 404ing -- so we walk pages collecting each card's detail
  URL into a set and stop once a couple of consecutive pages add nothing
  new (2026-09-24 probe: the whole live catalogue -- 48 shows -- was fully
  covered by page 1+2; page 3 onward added zero). Each card
  (div.item-type-4) is a title (h3.heading-6) + a detail link; the
  date/venue shown on the card is only the FIRST performance -- multi-date
  shows hide the rest behind a "その他日程をみる" toggle -- so listing
  cards are used ONLY to discover detail URLs, never for date/venue facts.

  DETAIL (one page per show): div.l-overview__info holds ONE OR MORE
  div.list-type-3 blocks, one per performance leg -- 日時 (date/time),
  開場／終演予定 (open / est. end -- there is no printed 開演/start time
  as its own field; 日時's own time IS the start time), 会場 ("<venue
  EnglishName>", a full-width ideographic space U+3000 always separates
  the JA/EN venue names). A MULTI-title "series" landing page (several
  different concerts bundled for a set-ticket promo, e.g. the seasonal
  "アフタヌーン・コンサート・シリーズ") labels each leg with its own
  p.list-type-3__ticket-title; a genuine multi-city SINGLE tour (e.g. one
  conductor + orchestra playing 3 halls) uses the SAME element to name
  that date's featured soloist instead -- both shapes are handled
  identically here (per-leg title overrides the page h1 when present).
  Because a bundled sub-concert is *also* separately listed under its own
  dedicated page (with the real per-seat pricing the bundle page lacks),
  yielding a leg from the bundle page too is safe by design: promoters.py
  folds same-source rows at the same (date, venue_key) whose titles
  overlap, so the two rows for one real show merge into one at export.

  Per-leg pricing lives in section.seats, grouped by h4.heading-5 (a date
  + the SAME per-leg subtitle as the list-type-3 title, disambiguating
  same-date multi-venue legs) > section.section-1 (a price CATEGORY, e.g.
  一般/学生割引/プレミアムシート -- one can be an age-restricted discount,
  see textutils.open_tier_min) > dl.list-type-4__item (one per seat
  class: dt seat name, dd price or "–" when not offered for that date). A
  seat's own <img alt="売り切れ"> marks it sold out (残席あり = available);
  a leg counts sold_out only when EVERY priced seat class is marked so.

  Ticket-sale history (ol.list-type-7, numbered "①M月D日(曜) HH:MMa.m.〜
  発売　<label>") and the "その他プレイガイド" playguide links (matched
  generically via tu.extract_ticket_links, P/Lコード included) are both
  PAGE-level, applied to every leg -- one promotion announcement, not
  per-leg windows.

  supports_detail = False: all enrichment happens inline in scrape(), one
  fetch per distinct show discovered on the listing pages.

  Lineup: a leg's own subtitle already names its featured performer(s)
  when the page has one ("北村陽(チェロ)、桑原志織(ピアノ)出演"); falling
  back to the page's own title otherwise -- the same title-as-lineup
  convention kajimoto/udo_artists/smash use.

Parsers key off the site's own content classes (list-type-3/-4, heading-*)
and Japanese field conventions (日時/会場/開場, 円 tiers), not incidental
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
from .base import BaseScraper, FetchError, NotFoundError

BASE = "https://www.japanarts.co.jp"
LISTING_URL = f"{BASE}/concert"

YMD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
H4_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s*(.*)$")
PRICE_YEN_RE = re.compile(r"([\d,，]+)\s*円")
_JA_SALE_LINE_RE = re.compile(
    r"(\d{1,2})月(\d{1,2})日(?:[（(][^）)]*[)）])?\s*"
    r"(\d{1,2})[：:](\d{2})\s*(a\.?m\.?|p\.?m\.?)?\s*[〜～~]?\s*発売\s*　?\s*(.*)$",
    re.I)


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


# --------------------------------------------------------------- listing
def parse_listing(html: str) -> list[str]:
    """Pure parse of one /concert (or /concert/page/N/) page -> absolute
    detail URLs (one per card). Cards carry only the FIRST leg's date/
    venue (multi-date shows hide the rest) so nothing else is extracted
    here -- see module docstring."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for card in soup.select("div.l-3col__item.filter-genre-target"):
        a = card.select_one(".item-type-4 > a[href]")
        if a is None:
            continue
        href = urljoin(LISTING_URL, a["href"])
        if href not in seen:
            seen.add(href)
            urls.append(href)
    return urls


# ----------------------------------------------------------------- detail
def parse_concert_page(html: str) -> dict:
    """Pure parse of one /concert/p<id>/ page -> {legs, sales}. `legs`:
    per-performance dicts (date, venue_raw, open_time, start_time,
    price_text, price_min, is_free, sold_out, title). `sales`: page-level
    ticket-sale history (applied to every leg by the caller)."""
    if not html:
        return {"legs": [], "sales": []}
    soup = BeautifulSoup(html, "lxml")
    full_text = soup.get_text(" ", strip=True)
    restricted = tu.restricted_tier_names(full_text)

    h1 = soup.select_one("h1.heading-9")
    page_title = _clean(h1.get_text(" ", strip=True)) if h1 else None

    price_by_key = _parse_price_sections(soup)

    legs: list[dict] = []
    info = soup.select_one(".l-overview__info")
    if info is not None:
        for block in info.select("div.list-type-3"):
            leg = _parse_leg(block, page_title, price_by_key, restricted)
            if leg:
                legs.append(leg)

    sales = _parse_sales(soup)
    ticket_links = tu.extract_ticket_links(soup, full_text)
    return {"legs": legs, "sales": sales, "ticket_links": ticket_links}


def _parse_leg(block, page_title: str | None, price_by_key: dict,
              restricted: set[str]) -> dict | None:
    # NOT run through _clean() here -- _clean() collapses ALL Unicode
    # whitespace (\s matches U+3000 too), which would destroy the
    # ideographic space separating the venue's JA/EN names below before
    # we get a chance to split on it.
    dls: dict[str, str] = {}
    for dl in block.select("dl.list-type-3__item"):
        dtag, ddtag = dl.find("dt"), dl.find("dd")
        if dtag and ddtag:
            dls.setdefault(_clean(dtag.get_text(strip=True)),
                          ddtag.get_text(" ", strip=True))

    dt_text = dls.get("日時")
    if not dt_text:
        return None
    m = YMD_RE.search(dt_text)
    if not m:
        return None
    try:
        date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None

    title_p = block.select_one("p.list-type-3__ticket-title")
    subtitle = _clean(title_p.get_text(" ", strip=True)) if title_p else ""
    # A same-day multi-performance page (2 recitals, same title, same
    # venue) sometimes labels each leg with nothing but its own showtime
    # ("13:00開演") instead of a real per-leg subtitle -- that alone would
    # make a useless Event title, so fall back to the page's own title.
    # `subtitle` itself (not this fallback) is still the price-section
    # match key below -- h4.heading-5 carries the SAME raw showtime text.
    display_subtitle = "" if re.fullmatch(r"\d{1,2}:\d{2}\s*開演", subtitle) \
        else subtitle
    title = display_subtitle or page_title

    open_time = start_time = None
    tm = re.search(r"(\d{1,2}:\d{2})", dt_text)
    if tm:
        start_time = tm.group(1)
    open_end = dls.get("開場 / 終演予定") or dls.get("開場／終演予定")
    if open_end:
        om = re.search(r"(\d{1,2}:\d{2})", open_end)
        if om:
            open_time = om.group(1)

    venue_full = dls.get("会場", "")
    venue_raw = _clean(venue_full.split("　")[0])
    if not venue_raw:
        return None

    entries, sold_flags = price_by_key.get((date, subtitle), ([], []))
    price_min = tu.open_tier_min(entries, restricted)
    price_text = "; ".join(f"{lbl} {yen:,}円" for lbl, yen in entries)[:300] or None
    is_free = (price_min == 0) if price_min is not None else None
    open_priced = [s for (lbl, _), s in zip(entries, sold_flags)
                   if not tu.is_restricted_tier(lbl)
                   and not any(n and n in lbl for n in restricted)]
    sold_out = bool(open_priced) and all(open_priced)

    return {
        "date": date, "venue_raw": venue_raw,
        "open_time": open_time, "start_time": start_time,
        "price_text": price_text, "price_min": price_min, "is_free": is_free,
        "sold_out": sold_out, "title": title,
    }


def _parse_price_sections(soup) -> dict[tuple[str, str], tuple[list, list]]:
    """{(date, subtitle): ([(label, yen), ...], [sold_out_bool, ...])} --
    subtitle is the h4's OWN trailing text after the date (the same
    per-leg subtitle list-type-3__ticket-title carries), which keeps two
    different-venue legs sharing one date from being blended together."""
    out: dict[tuple[str, str], tuple[list, list]] = {}
    for h4 in soup.select("section.seats h4.heading-5"):
        m = H4_DATE_RE.match(_clean(h4.get_text(" ", strip=True)))
        if not m:
            continue
        try:
            date = dt.date(int(m.group(1)), int(m.group(2)),
                           int(m.group(3))).isoformat()
        except ValueError:
            continue
        subtitle = _clean(m.group(4))
        date_section = h4.find_parent("section")
        if date_section is None:
            continue
        entries: list[tuple[str, int]] = []
        sold: list[bool] = []
        for cat in date_section.select("section.section-1"):
            h5 = cat.select_one("h5.heading-4")
            cat_name = _clean(h5.get_text(" ", strip=True)) if h5 else ""
            for dl in cat.select("div.list-type-4 dl.list-type-4__item"):
                dtag, ddtag = dl.find("dt"), dl.find("dd")
                if not dtag or not ddtag:
                    continue
                seat = _clean(dtag.get_text(" ", strip=True))
                price_txt = ddtag.get_text(" ", strip=True)
                ym = PRICE_YEN_RE.search(price_txt)
                if not ym:
                    continue
                try:
                    yen = int(re.sub(r"[,，]", "", ym.group(1)))
                except ValueError:
                    continue
                entries.append((f"{cat_name} {seat}".strip(), yen))
                sold.append(bool(ddtag.select_one('img[alt="売り切れ"]')))
        out[(date, subtitle)] = (entries, sold)
    return out


def _parse_sales(soup) -> list[dict]:
    """Page-level ticket-sale history from ol.list-type-7 -- numbered
    lines like '①4月11日(土) 10：00a.m.～発売　ジャパン･アーツぴあオンラ
    インチケット'. No closing time is ever printed (open-ended rounds), so
    every entry is opens-only. Bare '一般' labels are normalized to
    '一般発売' so textutils.sale_kind reads them as general on-sale."""
    entries: list[dict] = []
    for li in soup.select("ol.list-type-7 li.list-type-7__item"):
        text = _clean(li.get_text(" ", strip=True))
        m = _JA_SALE_LINE_RE.search(text)
        if not m:
            continue
        mo, da, hh, mm, ap, label = m.groups()
        hour = int(hh)
        ap = (ap or "").lower().replace(".", "")
        if ap == "pm" and hour != 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        opens = f"{int(mo):02d}-{int(da):02d} {hour:02d}:{mm}"
        label = label.strip() or "先行"
        if label == "一般":
            label = "一般発売"
        entries.append({"label": label, "opens": opens, "closes": None})
    return entries


def _event_sales(entries: list[dict], show_date: str) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        w = tu.sale_window(e["label"], e["opens"], e["closes"], show_date)
        if w and w not in out:
            out.append(w)
    return sorted(out, key=lambda e: e["opens"])


# --------------------------------------------------------------------- class
class JapanArtsScraper(BaseScraper):
    source_id = "japan_arts"
    source_name = "JAPAN ARTS"
    rate_limit_s = 2.0
    #: all enrichment happens inline in scrape(); the "detail" page IS
    #: what we fetch here -- there is no separate per-event detail pass.
    supports_detail = False

    #: politeness cap on listing pages walked -- the live catalogue
    #: (48 shows, 2026-09-24) was fully covered by page 1+2; this just
    #: bounds a pathological future growth in page count.
    def __init__(self, max_pages: int = 12, **kw):
        super().__init__(**kw)
        self.max_pages = max_pages
        #: raw venue strings we could not resolve (report only; extend
        #: venues.py to pick these up -- includes legitimately
        #: out-of-Kanto halls the nationwide roster plays, e.g. Fukuoka/
        #: Sapporo, which the operator can ignore).
        self.skipped_venues: set[str] = set()

    def scrape(self) -> Iterable[Event]:
        detail_urls: list[str] = []
        seen: set[str] = set()
        empty_streak = 0
        for i in range(1, self.max_pages + 1):
            url = LISTING_URL if i == 1 else f"{LISTING_URL}/page/{i}/"
            try:
                html = self.fetch(url)
            except NotFoundError:
                break
            new = [u for u in self.parse(html) if u not in seen]
            if not new:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0
            for u in new:
                seen.add(u)
                detail_urls.append(u)

        floor = tu.jst_today().isoformat()
        for detail_url in detail_urls:
            try:
                detail_html = self.fetch(detail_url)
            except FetchError:
                continue
            page = parse_concert_page(detail_html)
            yield from self._legs_to_events(page, detail_url, floor)

    def _legs_to_events(self, page: dict, detail_url: str,
                        floor: str) -> Iterator[Event]:
        kept: list[tuple[dict, str]] = []
        for leg in page["legs"]:
            if leg["date"] < floor:
                continue
            key = resolve_venue(leg["venue_raw"])
            if key is None:
                self.skipped_venues.add(leg["venue_raw"])
                continue
            kept.append((leg, key))

        date_counts: dict[str, int] = {}
        date_venue_counts: dict[tuple[str, str], int] = {}
        for leg, key in kept:
            date_counts[leg["date"]] = date_counts.get(leg["date"], 0) + 1
            dv = (leg["date"], key)
            date_venue_counts[dv] = date_venue_counts.get(dv, 0) + 1

        for leg, key in kept:
            title = leg["title"]
            date = leg["date"]
            if date_venue_counts[(date, key)] > 1:
                # a same-day, same-venue repeat (matinee + evening) needs
                # the start time too, or both legs collide on one fragment
                frag = f"#{date}-{key}-{leg['start_time'] or 'na'}"
            elif date_counts[date] > 1:
                frag = f"#{date}-{key}"
            else:
                frag = f"#{date}"
            nonmusic = tu.is_nonmusic(title or "")
            yield Event(
                source=self.source_id,
                source_url=detail_url + frag,
                title_ja=title,
                category=Category.OTHER if nonmusic else Category.MUSIC,
                genres=["classical"],
                start_date=date,
                open_time=leg["open_time"], start_time=leg["start_time"],
                venue_name=leg["venue_raw"],       # RAW; canonicalized at export
                price_text=leg["price_text"], price_min=leg["price_min"],
                is_free=leg["is_free"],
                is_sold_out=leg["sold_out"],
                ticket_links=page["ticket_links"],
                sales=_event_sales(page["sales"], date),
                lineup=[title] if title else [],
            )

    # -------------------------------------------------------- listing parse
    def parse(self, html: str, **context) -> list[str]:
        """Pure listing parse. Unlike other sources this yields detail
        URLs, not Events -- a listing card carries only the first leg's
        date/venue (see module docstring); scrape() fetches every
        distinct show's own page for the rest."""
        return parse_listing(html)
