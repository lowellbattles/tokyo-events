"""Scraper for Bunkamura オーチャードホール (Orchard Hall) — Shibuya.

Bunkamura's main complex (Theatre Cocoon, Le Cinéma, The Museum) has been
closed since 2023 for redevelopment, but Orchard Hall keeps operating and
sells through the operator's own ticketing subdomain
https://my.bunkamura.co.jp (東急文化村株式会社 — the official venue operator,
NOT a third-party agency).

Listing: ONE static server-rendered page holds ~a year of shows, no
pagination:
  https://my.bunkamura.co.jp/Show/programFacilityList/index/01   (01 = 公演)
It is a MULTI-VENUE genre listing — the same table also carries Tokyu
Theatre Orb, Cerulian Tower Noh Theater, etc. Each <tr> is:
  <td class="large01 text-bold">title</td>
  <td class="pr5">2026年7月19日（日） <br/>〜 2026年7月20日（月祝）</td>  (date/range)
  <td>会場</td>                                                     (venue)
  <td class="align-center">… <img id="btnDetailOrch_NNNN" val="NNNN"></td>
We anchor on the detail-button `val` (the real detail id — the visible
href is javascript:void(0)) and keep only rows whose venue cell is exactly
"オーチャードホール".

Titles use half-width katakana + full-width digits (東京ﾌｨﾙ 第１０３５回…) so
they are NFKC-normalized. Dates are absolute (year present) → no year
inference. The listing has NO times/prices; those come from the detail
pass.

Detail: https://my.bunkamura.co.jp/ticket/ProgramDetail/index/{id} carries a
公演日時 table (開演時間 START times, ◎○△× seat availability) and a 席種 box
of ¥ tiers. The generic parse_detail keys off English OPEN/START, which this
page does not use (開演/開場), so parse_detail is overridden here.

Category policy (Bunkamura is a mixed classical/ballet/pop hall): ballet and
opera are Category.OTHER for now; orchestral / recital / pop concerts are
Category.MUSIC with genre tagging left to export.
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

# Ballet / opera → OTHER (per venue category policy). Deliberately narrow;
# broad music keyword lists are avoided — everything else at this hall is a
# concert. tu.is_nonmusic backstops the arena-style non-concert rows.
NONCONCERT_RE = re.compile(r"バレエ|BALLET|オペラ|OPERA|歌劇", re.I)
RANGE_SEP_RE = re.compile(r"[〜～~]")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


class OrchardHallScraper(BaseScraper):
    source_id = "orchard_hall"
    source_name = "Bunkamura Orchard Hall"
    BASE = "https://my.bunkamura.co.jp"
    LIST_URL = "https://my.bunkamura.co.jp/Show/programFacilityList/index/01"
    #: only rows whose 会場 cell equals this belong to Orchard Hall. Sibling
    #: Bunkamura venues share the same listing and could get their own
    #: source_ids later by swapping this string.
    VENUE_MATCH = "オーチャードホール"
    VENUE = dict(
        venue_name="Bunkamura オーチャードホール",
        venue_area="Shibuya",
        address="2-24-1 Dogenzaka, Shibuya-ku, Tokyo",
        lat=35.6595, lng=139.6968,
    )

    def scrape(self) -> Iterable[Event]:
        # Single static page, no pagination — holds ~1 year of shows.
        yield from self.parse(self.fetch(self.LIST_URL))

    # ------------------------------------------------------------------ #
    # listing
    # ------------------------------------------------------------------ #
    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        for img in soup.find_all("img"):
            vid = img.get("val")
            if not vid or not str(img.get("id", "")).startswith("btnDetail"):
                continue
            row = img.find_parent("tr")
            if row is None:
                continue
            ev = self._parse_row(row, vid)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_row(self, row, vid: str) -> Event | None:
        title_td = row.find("td", class_="large01")
        date_td = row.find("td", class_="pr5")
        # Venue is the one bare <td> (no class) among the row's own cells.
        venue_td = next(
            (td for td in row.find_all("td", recursive=False)
             if not td.get("class")), None)
        if not (title_td and date_td and venue_td):
            return None            # structure churned -> stay loud (drop row)

        if venue_td.get_text(strip=True) != self.VENUE_MATCH:
            return None            # sibling Bunkamura venue

        title = _nfkc(title_td.get_text(" ", strip=True))
        if not title:
            return None

        cell = _nfkc(date_td.get_text(" ", strip=True))
        dates = []
        for y, mo, d in tu.FULL_DATE_RE.findall(cell):
            try:
                dates.append(dt.date(int(y), int(mo), int(d)).isoformat())
            except ValueError:
                pass
        if not dates:
            return None            # no parseable date -> drop (loud)
        start_date = dates[0]
        end_date = dates[-1] if (len(dates) > 1 and RANGE_SEP_RE.search(cell)) \
            else None

        url = urljoin(self.BASE, f"/ticket/ProgramDetail/index/{vid}")
        cat = (Category.OTHER
               if (NONCONCERT_RE.search(title) or tu.is_nonmusic(title))
               else Category.MUSIC)
        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=cat,
            start_date=start_date, end_date=end_date,
            **self.VENUE,
        )

    # ------------------------------------------------------------------ #
    # detail — the ticketing page uses 開演/開場 + 席種 ¥ tiers, not the
    # English OPEN/START the generic parse_detail expects.
    # ------------------------------------------------------------------ #
    def parse_detail(self, html: str, ev: Event) -> Event:
        soup = BeautifulSoup(html, "lxml")
        full = soup.get_text(" ", strip=True)

        sched = soup.find("table", summary="公演日時")

        # START time: first 開演時間 in the 公演日時 table (representative for
        # multi-stage/day runs). The 備考 column holds artist names, so the
        # first HH:MM in reading order is always the first stage's start.
        if not ev.start_time and sched is not None:
            m = TIME_RE.search(sched.get_text(" ", strip=True))
            if m:
                ev.start_time = f"{int(m.group(1)):02d}:{m.group(2)}"

        # Doors: the page states "開場は開演の30分前" (doors = start − 30min).
        if not ev.open_time and ev.start_time and "開場は開演の30分前" in full:
            ev.open_time = _minus_minutes(ev.start_time, 30)

        # Prices: prefer the 席種（すべて税込）box of "19,000円" tiers; fall
        # back to ¥ tiers near a 料金 marker (avoids stray merch amounts).
        if ev.price_min is None:
            amounts, box_text = _seat_amounts(soup)
            if amounts:
                ev.price_min = min(amounts)
                ev.is_free = ev.price_min == 0
                ev.price_text = re.sub(r"\s+", " ", box_text).strip()[:300]
            else:
                m = re.search(r"(?:料金|TICKET|チケット)(.{0,250})", full, re.S)
                zone = m.group(1) if m else full
                ev.price_text, ev.price_min, ev.is_free = tu.parse_prices(zone)

        # Sold out only when the show is FULLY gone: an explicit 完売/SOLD OUT,
        # or every seat glyph in the 公演日時 table is × / － (取扱い無し) with
        # no ◎○△ availability left. Partial × is not a sell-out.
        if not ev.is_sold_out:
            if tu.SOLD_OUT_RE.search(full):
                ev.is_sold_out = True
            elif sched is not None:
                glyphs = set(re.findall(r"[◎○△×]", sched.get_text()))
                if "×" in glyphs and not (glyphs & {"◎", "○", "△"}):
                    ev.is_sold_out = True
        return ev


def _minus_minutes(hhmm: str, mins: int) -> str | None:
    m = TIME_RE.match(hhmm)
    if not m:
        return None
    total = int(m.group(1)) * 60 + int(m.group(2)) - mins
    if total < 0:
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


def _seat_amounts(soup) -> tuple[list[int], str]:
    """Yen tiers from the 席種（すべて税込）box, e.g. 'Ｓ席：19,000円 …'."""
    for h in soup.find_all(["h3", "h4"]):
        if "席種" in h.get_text():
            box = h.find_next("div")
            if box is None:
                break
            text = box.get_text(" ", strip=True)
            amounts = [int(x.replace(",", ""))
                       for x in re.findall(r"([\d,]+)\s*円", text)]
            return amounts, text
    return [], ""
