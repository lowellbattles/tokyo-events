"""Scraper for CLUB CITTA' (クラブチッタ) — https://clubcitta.co.jp

Kawasaki live hall (~1,300 cap, La Cittadella complex, 5 min from Kawasaki
Station). The public schedule at /schedule is plain server-rendered HTML —
no JS execution needed. Month pagination is a query string:
    /schedule?year=YYYY&month=Mon   (month = 3-letter English abbrev)
Bare /schedule serves the current month. On the current-month page the
already-past days of the month are still listed (styled ``item fix``) after
the upcoming ones — they carry valid past dates; the export/date filter
drops them. Explicit month pages list only that month.

Listing card (verbatim shape, div.schedule_list > div.item):
    <a href="https://clubcitta.co.jp/schedule/1686">
      <div class="item_detail"><p>13<span>Mon</span></p> ...img... </div>
      <div class="txt_wrap">
        <h3>音楽劇場-SP Vol.3-</h3>
        <p>OPEN 15:30 / START 16:00<br><br>【出演】Bestted / ...</p>
      </div>
    </a>
Each card gives only day-of-month + English weekday; the month/year comes
from the page's ?year=&month= context (pinned via the ``month`` kwarg),
exactly like the Zepp month-pagination pattern. Times are OPEN/START (the
venue never uses 開場/開演). Lineup follows the time line, often after
【出演】/【O.A】/【MC】/【DJ】 markers, slash-separated.

Prices live only on the detail page and use the "N,NNN円(税込)" convention
(NO ¥ symbol), so the generic base.parse_detail() — which keys off ¥ — is
overridden here to read the 座種/料金 table row and the standard eplus/pia/
lawson ticket links.

Non-event rows: "PRIVATE" / 貸し切り利用日 are private-rental (venue closed
to the public) and are skipped entirely. tu.is_nonmusic() still tags any
clearly non-concert row as Category.OTHER (precision-first).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Detail links are /schedule/<numeric id>. Anchored so the calendar's
# ?date= / ?year= nav links and unrelated paths never match.
DETAIL_HREF_RE = re.compile(r"/schedule/(\d+)/?$")
# The day cell: "<p>13<span>Mon</span></p>" -> text "13Mon".
DAY_DOW_RE = re.compile(
    r"^\s*(\d{1,2})\s*(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s*$", re.I)
# Venue's own non-event business (closed to the public) — skip entirely.
PRIVATE_RE = re.compile(r"貸し?切り|PRIVATE", re.I)

# Price parsing (detail page): "N,NNN円", no ¥ symbol.
YEN_KANJI_RE = re.compile(r"([\d,]+)\s*円")
DRINK_CHARGE_RE = re.compile(r"ドリンク代?\s*[\d,]+\s*円")
FREE_RE = re.compile(r"入場無料|入場料無料|無料", re.I)

# Lines inside a card body that are NOT lineup (times, caveats, promo, info).
_NON_LINEUP_RE = re.compile(
    r"OPEN|START|CLOSE|開場|開演|\d{1,2}[:：]\d{2}|～|"
    r"SOLD\s*OUT|THANK\s*YOU|完売|"
    r"予告なく|入場不可|詳細は後日|お楽しみ|未定|貸し?切り|"
    r"^※|^■|^●|^-|SUPPORT\s*ACT|and\s*more|FC限定", re.I)
# A short leading bracketed label to strip: 【出演】《…》[…]＜…＞（…）
_LABEL_PREFIX_RE = re.compile(r"^\s*[【《\[＜(][^】》\]＞)]{0,14}[】》\]＞)]\s*")


class ClubCittaScraper(BaseScraper):
    source_id = "club_citta"
    source_name = "CLUB CITTA'"
    BASE = "https://clubcitta.co.jp"

    # Kawasaki, La Cittadella. No postal address is published on the
    # schedule/detail/access pages, so address is left unset. lat/lng are
    # the venue's known location.
    VENUE = dict(
        venue_name="CLUB CITTA'（クラブチッタ）",
        venue_area="Kawasaki",
        address=None,
        lat=35.5308,
        lng=139.6994,
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
            if i == 0:
                url = f"{self.BASE}/schedule"          # current month
            else:
                url = f"{self.BASE}/schedule?year={m.year}&month={MONTH_ABBR[m.month - 1]}"
            try:
                html = self.fetch(url)
            except RuntimeError:
                break
            fresh = [e for e in self.parse(html, month=m)
                     if e.source_url not in seen]
            seen.update(e.source_url for e in fresh)
            # A real future month can legitimately be empty; stop after two
            # consecutive empties rather than walking the whole calendar.
            empty_streak = 0 if fresh else empty_streak + 1
            if i and empty_streak >= 3:
                break
            yield from fresh

    # ------------------------------------------------------------ pure parse
    def parse(self, html: str, month: dt.date | None = None,
              today: dt.date | None = None, **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        # Scope to the schedule list so footer/banner /schedule/<id> links are
        # excluded. If the class churns, fall back to the whole document; the
        # per-card day-cell guard below still rejects non-event anchors.
        scope = soup.find("div", class_="schedule_list") or soup
        events: dict[str, Event] = {}
        for a in scope.find_all("a", href=DETAIL_HREF_RE):
            url = urljoin(self.BASE, a["href"].split("?")[0].rstrip("/"))
            ev = self._parse_card(a, url, month, today)
            if ev and ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _parse_card(self, a, url: str, month: dt.date | None,
                    today: dt.date | None) -> Event | None:
        # --- day cell -> date (month/year from the pinned page context) ---
        day = None
        for p in a.find_all("p"):
            m = DAY_DOW_RE.match(p.get_text(strip=True))
            if m:
                day = int(m.group(1))
                break
        if day is None:
            return None
        ctx = month or dt.date.today().replace(day=1)
        try:
            date = dt.date(ctx.year, ctx.month, day).isoformat()
        except ValueError:
            return None

        # --- title (h3) ---
        h3 = a.find("h3")
        title = h3.get_text(" ", strip=True) if h3 else None
        if not title:
            return None
        title = re.sub(r"\s+", " ", title).strip()

        # --- body text (everything in txt_wrap except the title) ---
        tw = a.find("div", class_="txt_wrap") or a
        parts = []
        for child in tw.children:
            if getattr(child, "name", None) == "h3":
                continue
            if hasattr(child, "get_text"):
                t = child.get_text("\n", strip=True)
            else:
                t = str(child).strip()
            if t:
                parts.append(t)
        body = "\n".join(parts)

        # --- skip the venue's own non-event days ---
        if PRIVATE_RE.search(title) or PRIVATE_RE.search(body):
            return None

        open_time, start_time = tu.parse_times(body)
        lineup = _extract_lineup(body)

        category = Category.MUSIC
        if tu.is_nonmusic(title + " " + " ".join(lineup)):
            category = Category.OTHER

        return Event(
            source=self.source_id, source_url=url,
            title_ja=title, category=category, start_date=date,
            open_time=open_time, start_time=start_time, lineup=lineup,
            is_sold_out=bool(tu.SOLD_OUT_RE.search(body)),
            venue_name=self.VENUE["venue_name"],
            venue_area=self.VENUE["venue_area"],
            address=self.VENUE["address"],
            lat=self.VENUE["lat"], lng=self.VENUE["lng"],
        )

    # ------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """CLUB CITTA' prices use "N,NNN円" (no ¥), so the ¥-based generic
        detail parser can't read them — read the 座種/料金 table row here.
        Times / ticket links / sold-out use the same conventions as base."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        if not (ev.open_time or ev.start_time):
            et = soup.find("table", class_="event_table")
            zone = et.get_text(" ", strip=True) if et else text
            ev.open_time, ev.start_time = tu.parse_times(zone)

        if ev.price_min is None:
            ev.price_text, ev.price_min, ev.is_free = _parse_price(soup, text)

        if not ev.ticket_links:
            ev.ticket_links = _ticket_links(soup, text)

        if not ev.is_sold_out and tu.SOLD_OUT_RE.search(text):
            ev.is_sold_out = True
        return ev


def _extract_lineup(body: str) -> list[str]:
    """Best-effort performer list from a card body. Conservative: drops
    time/caveat/promo/info lines, strips a leading 【出演】-style label, then
    splits the remainder on slashes. Complex multi-section bills may yield a
    partial list; refined later in the artist-crossref phase."""
    names: list[str] = []
    for raw in body.split("\n"):
        line = raw.replace("\xa0", " ").strip()
        if not line or _NON_LINEUP_RE.search(line):
            continue
        line = _LABEL_PREFIX_RE.sub("", line)
        if not line:
            continue
        for tok in re.split(r"[/／]", line):
            tok = tok.strip(" ・,、").strip()
            if "：" in tok or ":" in tok:      # "GUEST：X" -> "X"
                tok = re.split(r"[:：]", tok)[-1].strip()
            if tok and len(tok) <= 40:
                names.append(tok)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:40]


def _parse_price(soup, page_text: str) -> tuple[str | None, int | None, bool | None]:
    """Read the 座種/料金 data-table row; parse "N,NNN円" tiers, excluding the
    per-person drink charge (ドリンク代NNN円)."""
    zone = None
    table = soup.find("table", class_="data_table")
    if table:
        for tr in table.find_all("tr"):
            th = tr.find("th")
            if th and "料金" in th.get_text():
                td = tr.find("td")
                if td:
                    zone = td.get_text("\n", strip=True)
                break
    if zone is None:
        m = re.search(r"(?:座種|料金|前売)(.{0,400})", page_text, re.S)
        zone = m.group(1) if m else ""

    cleaned = DRINK_CHARGE_RE.sub("", zone)
    yen = [int(x.replace(",", "")) for x in YEN_KANJI_RE.findall(cleaned)]
    price_text = re.sub(r"\s+", " ", zone).strip()[:300] or None
    if yen:
        pmin = min(yen)
        return price_text, pmin, pmin == 0
    if FREE_RE.search(zone):
        return price_text, 0, True
    return price_text, None, None


def _ticket_links(soup, page_text: str) -> list[dict]:
    """Standard playguide links (eplus/pia/lawson/...) plus any anchors in the
    venue's own <ul class="ticket_link"> section, deduped by URL."""
    links = tu.extract_ticket_links(soup, page_text)
    have = {l.get("url") for l in links}
    for a in soup.select("ul.ticket_link a[href]"):
        href = a["href"].strip()
        if not href.startswith("http") or href in have:
            continue
        provider = None
        for dom, prov in tu.TICKET_PROVIDERS.items():
            if dom in href:
                provider = prov
                break
        if provider is None:
            provider = (a.get_text(strip=True) or "ticket").lower()[:20]
        links.append({"provider": provider, "url": href, "code": None})
        have.add(href)
    return links
