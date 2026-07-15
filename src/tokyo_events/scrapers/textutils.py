"""Shared parsing helpers for Japanese live-house schedule pages.

Conventions these encode (near-universal across Tokyo venue sites):
- OPEN/START times as "OPEN 18:00 / START 19:00" (or [OPEN]/[START])
- Prices as ¥N,NNN with tiered seating; ADV/前売 marks advance price blocks
- Dates as 2026.7.3 / 7.3(金) / 7/3 fri, sometimes without a year
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

OPEN_START_COMBINED_RE = re.compile(r"OPEN\s*/\s*START[\]】\s]*(\d{1,2}:\d{2})", re.I)
OPEN_RE = re.compile(r"\[?OPEN[\]】\s:]*(\d{1,2}:\d{2})", re.I)
START_RE = re.compile(r"\[?START[\]】\s:]*(\d{1,2}:\d{2})", re.I)
YEN_RE = re.compile(r"[¥￥]\s*([\d,，]+)")
FULL_DATE_RE = re.compile(r"(20\d{2})[./年\s]{1,2}(\d{1,2})[./月\s]{1,2}(\d{1,2})")
MONTH_DAY_RE = re.compile(
    r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*[(（]?(sun|mon|tue|wed|thu|fri|sat|日|月|火|水|木|金|土)",
    re.I,
)
# 完売 also covers 完売御礼/チケット完売. 予定枚数終了 must be in its
# announcement form (…終了しました) — the bare phrase appears in seat-
# availability LEGENDS (×：予定枚数終了) and conditional boilerplate
# (予定枚数終了次第). 受付終了 is deliberately NOT here — it often means
# "advance sales closed", not sold out.
SOLD_OUT_RE = re.compile(
    r"SOLD[\s-]*OUT|ソールドアウト|完売|"
    r"予定枚数[がはに]?終了(?:しました|いたしました|致しました)|満員御礼", re.I)

# Arena/hall calendars mix in sports, ice shows, ceremonies, fashion shows
# and trade events. Deliberately precision-first: better to let an odd one
# through than hide a real concert.
NONMUSIC_RE = re.compile(
    r"ディズニー・?オン・?アイス|DISNEY\s*ON\s*ICE|アイスショー|ON\s*ICE\b|"
    r"フィギュアスケート|Bリーグ|B\.LEAGUE|SVリーグ|Tリーグ|Vリーグ|"
    # combat sports: keyed to promotions/explicit match words, NOT the bare
    # nouns (プロレス/ボクシング appear inside real idol/music titles, and
    # K-1 hid "WORK-1st Anniversary LIVE" as a substring)
    r"大相撲|(?:新日本|全日本)プロレス|プロレスリング|"
    r"タイトルマッチ|世界戦|(?<![0-9A-Za-z])RIZIN|(?<![0-9A-Za-z])K-1(?![0-9A-Za-z])|"
    r"卓球|バレーボール|バスケットボール|ハンドボール|"
    r"世界選手権|全日本選手権|"
    r"式典|入学式|卒業式|入社式|株主総会|表彰式|説明会|業界研究|"
    r"東京ガールズコレクション|ガールズアワード|GirlsAward|"
    r"展示会|見本市|即売会", re.I)


def is_nonmusic(text: str) -> bool:
    """True when an event title/summary is clearly not a concert."""
    return bool(NONMUSIC_RE.search(text))
REPEATED_TITLE_RE = re.compile(r"^(.{2,}?)\1+", re.S)

# Playguide / ticketing domains -> provider ids
TICKET_PROVIDERS = {
    "eplus.jp": "eplus",
    "t.pia.jp": "pia",
    "w.pia.jp": "pia",
    "l-tike.com": "lawson",
    "ticket.rakuten": "rakuten",
    "zaiko.io": "zaiko",
    "t.livepocket.jp": "livepocket",
    "tiget.net": "tiget",
    "ticketmaster.co.jp": "ticketmaster",
}
P_CODE_RE = re.compile(r"[PＰ]コード[:：\s]*([\d-]{4,10})")
L_CODE_RE = re.compile(r"[LＬ]コード[:：\s]*([\d-]{4,10})")

#: playguide hosts that never identify an event (help desks, FAQ)
_NONEVENT_TICKET_HOST_RE = re.compile(r"^(?:support|faq|info|help)\.", re.I)
#: provider section roots with no event identity ("eplus.jp/sf/", "w.pia.jp/t/")
_GENERIC_TICKET_PATHS = {"t", "sf", "interpia"}


def is_generic_ticket_url(url: str) -> bool:
    """True for playguide links that point at the provider itself rather
    than an event — bare homepages (http://eplus.jp/), section roots and
    support/FAQ subdomains. Venue pages love placing these next to real
    event links, and a homepage is worse than no link. Short paths like
    eplus.jp/9mm are real artist slugs and must survive."""
    try:
        p = urlparse(url)
    except ValueError:                          # pragma: no cover
        return True
    if _NONEVENT_TICKET_HOST_RE.match(p.netloc or ""):
        return True
    path = (p.path or "").strip("/")
    if not path and not p.query:
        return True
    return path.lower() in _GENERIC_TICKET_PATHS and not p.query


def first(pattern: re.Pattern, s: str) -> str | None:
    m = pattern.search(s)
    return m.group(1).strip() if m else None


def parse_times(text: str) -> tuple[str | None, str | None]:
    """Return (open_time, start_time) from a schedule block."""
    combined = OPEN_START_COMBINED_RE.search(text)
    if combined:
        return combined.group(1), combined.group(1)
    return first(OPEN_RE, text), first(START_RE, text)


#: "drink charge" notes (ドリンク代別途￥600, +1DRINK ¥600, D代600円 …) —
#: a mandatory venue fee that is NOT the ticket price, yet carries a ¥
#: amount that would otherwise win min-price parsing.
DRINK_CHARGE_RE = re.compile(
    r"(?:別途)?\s*(?:[1１]?ドリンク代?|DRINK|[1１]D(?:RINK)?代?|Ｄ代|D代)\s*"
    r"(?:別途|別|代)?\s*[:：]?\s*[¥￥]?\s*[\d,，]{3,}\s*(?:円)?", re.I)


def strip_drink_charges(text: str) -> str:
    """Remove drink-charge amounts so they can't undercut the real ticket
    floor when taking min() over ¥ amounts."""
    return DRINK_CHARGE_RE.sub(" ", text)


def parse_prices(text: str) -> tuple[str | None, int | None, bool | None]:
    """Return (price_text, price_min, is_free) from a block of price tiers."""
    yen = [int(x.replace(",", "").replace("，", "")) for x in YEN_RE.findall(text)]
    if not yen:
        return None, None, None
    pmin = min(yen)
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:300], pmin, pmin == 0


def infer_year(month: int, day: int, today: dt.date | None = None) -> str | None:
    """Given a month/day with no year, pick the year that makes the date fall
    within [today - 60d, today + ~10 months]. Venue schedules are
    forward-looking, so a date far in the past means next year."""
    today = today or dt.date.today()
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            cand = dt.date(year, month, day)
        except ValueError:
            continue
        if -60 <= (cand - today).days <= 320:
            return cand.isoformat()
    return None


def add_months(d: dt.date, n: int) -> dt.date:
    """Month arithmetic for month-page pagination (day preserved as d.day
    only when valid; callers normally pass a first-of-month date)."""
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1)


def parse_date(text: str, today: dt.date | None = None) -> str | None:
    """Extract the first plausible event date from a block of text."""
    m = FULL_DATE_RE.search(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = MONTH_DAY_RE.search(text)
    if m:
        return infer_year(int(m.group(1)), int(m.group(2)), today)
    return None


def split_repeated_title(head: str) -> tuple[str, str | None]:
    """Listing blocks often repeat the title (img alt + heading).
    'XXY' -> ('X', 'Y')."""
    m = REPEATED_TITLE_RE.match(head)
    if m:
        title = m.group(1).strip()
        rest = head[m.end():].strip(" -–—|・").strip()
        return title, rest or None
    return head.strip(), None


def extract_ticket_links(soup, page_text: str = "") -> list[dict]:
    """Pull playguide links + P/L codes out of a (detail) page."""
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, provider in TICKET_PROVIDERS.items():
            if domain in href and (provider, href) not in seen \
                    and not is_generic_ticket_url(href):
                seen.add((provider, href))
                links.append({"provider": provider, "url": href, "code": None})
                break
    text = page_text or soup.get_text(" ", strip=True)
    pcode, lcode = first(P_CODE_RE, text), first(L_CODE_RE, text)
    if pcode:
        links.append({"provider": "pia", "url": None, "code": f"P{pcode}"})
    if lcode:
        links.append({"provider": "lawson", "url": None, "code": f"L{lcode}"})
    return links
