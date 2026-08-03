"""Export-time handling of promoter-sourced events.

Promoter calendars (Sogo Tokyo, Creativeman) overlap the venue calendars
we scrape directly — the venue's own record is authoritative for its own
schedule, but the promoter often knows things the venue page doesn't
(listing-level SOLD OUT badges, playguide links). At export:

1. every event gets a `venue_key` — the canonical venue identity the
   frontend groups by. Venue-scraped sources keep their source id;
   promoter events resolve their raw venue string via venues.resolve_venue.
2. a promoter event that duplicates a venue event (same date + venue_key
   + artist/title overlap) is MERGED into the venue event (sold-out flag,
   ticket-link union, gap-fill for times/prices) and dropped from the
   feed.
3. promoter events at gap venues (日本武道館 ...) stay standalone, giving
   the site coverage no venue scraper can. Duplicate promoter rows for
   the SAME show fold together first (R6): co-promotions merge on artist
   overlap; one promoter's per-performance pages merge only when their
   titles align and their start times don't disagree.

Like genres/artists, this runs at export only — the DB keeps pure
per-source facts and alias updates re-resolve without re-scraping.
"""

from __future__ import annotations

import datetime as dt
import re

from .artists import canonical_spelling, norm_key
from .venues import resolve_venue

PROMOTER_SOURCES = {"sogo_tokyo", "creativeman", "smash_jpn", "udo_artists",
                    "disk_garage", "livenation_jp"}
FESTIVAL_SOURCE = "festivals"
#: curated seasonal sources — like festivals, each event's venue_name IS a
#: canonical identity that must become its venue_key
SEASONAL_SOURCES = {"matsuri", "hanabi", "flowers"}

#: venue keys that HOST a festival we cover — only rows at these venues are
#: candidates for festival dedupe (an after-party at a club with the
#: festival's name in its title must NOT be folded away)
FESTIVAL_HOSTS = {
    "summer_sonic_tokyo": {"makuhari_messe", "zozo_marine_stadium"},
    "countdown_japan": {"makuhari_messe"},
}


#: after norm_key's NFKC every paren is ASCII — parenthesized segments
#: are reading aids / annotations, never identity (same rule as
#: venues.norm_venue): 高城れに(ももいろクローバーZ), Snugs(スナッグス)
_PAREN_SEG_RE = re.compile(r"\([^()]*\)")
#: decorative characters the two sides of a merge disagree on. NFKC
#: folds ～(FF5E) to ~ but leaves 〜(U+301C) alone — fold both to one
#: form; middle dots, curly/corner quotes and ®-class marks just drop.
_FOLD_TABLE = str.maketrans({
    "〜": "~", "・": None, "®": None, "™": None, "©": None,
    "“": None, "”": None, "‘": None, "’": None, '"': None, "'": None,
    "「": None, "」": None, "『": None, "』": None})
_WS_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")   # kana + ideographs


def _match_norm(s: str | None) -> str:
    """Aggressive fold for overlap TESTING only — never for storage or
    display. Venue and promoter spell the same show differently in
    exactly these ways: internal spacing, 〜/～ variants, decorative
    punctuation, parenthesized reading aids (roadmap R5 / DUP-1).
    Curated JA↔EN aliases fold first (R24): a venue's アン・ウィルソン
    and a promoter's ANN WILSON are the same needle."""
    s = canonical_spelling((s or "").strip())
    s = norm_key(s)
    s = _PAREN_SEG_RE.sub("", s)
    s = s.translate(_FOLD_TABLE)
    return _WS_RE.sub("", s)


def _min_needle(n: str) -> int:
    """CJK is dense — 3 chars is already a name (超特急); Latin needs 4
    to stay safe as a substring needle."""
    return 3 if _CJK_RE.search(n) else 4


def _artist_overlap(promo: dict, venue_ev: dict) -> bool:
    """True when the promoter row and the venue row plausibly describe the
    same show: any promoter act/title appears in the venue event's
    title/lineup, any venue lineup act in the promoter's text, or the
    venue's own title inside the promoter's text (promoter rows are
    often "<venue title> + suffix" supersets: 超特急 東京ドーム公演).
    All comparisons happen in _match_norm space; the \\x00 joins keep a
    needle from matching across two fields' boundary."""
    v_titles = [venue_ev.get("title_ja"), venue_ev.get("title_en")]
    v_hay = "\x00".join(_match_norm(p) for p in (
        *v_titles, *(venue_ev.get("lineup") or [])) if p)
    p_names = [n for n in (promo.get("lineup") or []) if n]
    p_titles = [t for t in (promo.get("title_ja"), promo.get("title_en")) if t]
    p_hay = "\x00".join(_match_norm(p) for p in (*p_names, *p_titles))

    for name in p_names + p_titles:
        n = _match_norm(name)
        if len(n) >= 3 and n in v_hay:
            return True
    for name in (venue_ev.get("lineup") or []):
        n = _match_norm(name)
        if len(n) >= 3 and n in p_hay:
            return True
    for title in v_titles:
        n = _match_norm(title)
        if n and len(n) >= _min_needle(n) and n in p_hay:
            return True
    return False


def _times_compatible(a: dict, b: dict) -> bool:
    """Two-performances guard (R6): same-day rows with two DIFFERENT
    stated start times are two shows (昼/夜公演), never duplicates. A
    missing time on either side is treated as compatible — venue sources
    model one row per day, so per-day folding matches their granularity."""
    ta, tb = a.get("start_time"), b.get("start_time")
    return ta is None or tb is None or ta == tb


def _titles_overlap(a: dict, b: dict) -> bool:
    """Same-source fold guard (R6): one promoter's two rows are one show
    only when their TITLES align (identical or one contains the other).
    A promoter knows its own catalog — two distinct titles for the same
    artist/venue/day are two distinct things (初音ミク's マジカルミライ
    ＜ライブ＞ vs ＜企画展＞ at Makuhari), where artist overlap alone
    would wrongly glue them."""
    ta = [_match_norm(t) for t in (a.get("title_ja"), a.get("title_en")) if t]
    tb = [_match_norm(t) for t in (b.get("title_ja"), b.get("title_en")) if t]
    for x in ta:
        for y in tb:
            needle, hay = (x, y) if len(x) <= len(y) else (y, x)
            if needle and len(needle) >= _min_needle(needle) \
                    and needle in hay:
                return True
    return False


def _merge(into: dict, promo: dict) -> None:
    """Enrich a venue event with what the promoter knows. Facts only,
    fill-don't-overwrite — except sold-out, which ORs (a promoter's
    SOLD OUT badge is a positive signal the venue page may lack)."""
    if promo.get("is_sold_out"):
        into["is_sold_out"] = True
    have = {t.get("url") for t in into.get("ticket_links") or [] if t.get("url")}
    for t in promo.get("ticket_links") or []:
        if t.get("url") and t["url"] not in have:
            into.setdefault("ticket_links", []).append(t)
            have.add(t["url"])
    for f in ("open_time", "start_time", "price_text", "price_min",
              "is_free", "ticket_url"):
        if into.get(f) in (None, []) and promo.get(f) not in (None, []):
            into[f] = promo[f]


def apply_promoter_merge(events: list[dict]) -> list[dict]:
    """Set venue_key on every event and fold duplicate promoter rows into
    their venue-source counterparts. Returns the (possibly shorter) event
    list for export. Never raises."""
    try:
        return _apply(events)
    except Exception as e:                       # pragma: no cover
        print(f"promoter merge failed ({e}); exporting unmerged")
        for d in events:
            d.setdefault("venue_key", d["source"])
        return events


def _dates_of(d: dict) -> list[str]:
    start, end = d.get("start_date"), d.get("end_date") or d.get("start_date")
    if not start:
        return []
    try:
        s = dt.date.fromisoformat(start)
        e = dt.date.fromisoformat(end)
    except ValueError:
        return [start]
    return [(s + dt.timedelta(days=i)).isoformat()
            for i in range((e - s).days + 1)]


def _festival_windows(events: list[dict]) -> dict[str, tuple[str, set[str]]]:
    """festival venue_key -> (normalized base name, covered dates)."""
    wins: dict[str, tuple[str, set[str]]] = {}
    for d in events:
        if d["source"] != FESTIVAL_SOURCE:
            continue
        key = d["venue_key"]
        base = norm_key((d.get("venue_name") or "").split("(")[0])
        name, dates = wins.get(key, (base, set()))
        dates.update(_dates_of(d))
        wins[key] = (name or base, dates)
    return wins


def _is_festival_duplicate(d: dict,
                           wins: dict[str, tuple[str, set[str]]]) -> bool:
    """True when a non-festival row is the festival itself seen through its
    host venue's (or its promoter's) calendar: named after the festival,
    dated inside its window, and — for venue rows — AT a known host venue."""
    title = norm_key(f"{d.get('title_ja') or ''} {d.get('title_en') or ''}")
    for fest_key, (name, dates) in wins.items():
        if not name or name not in title:
            continue
        if d.get("start_date") not in dates:
            continue
        hosts = FESTIVAL_HOSTS.get(fest_key, set())
        if d["source"] in PROMOTER_SOURCES or d["venue_key"] in hosts:
            return True
    return False


def _apply(events: list[dict]) -> list[dict]:
    by_date_venue: dict[tuple[str, str], list[dict]] = {}
    for d in events:
        if d["source"] == FESTIVAL_SOURCE or d["source"] in SEASONAL_SOURCES:
            d["venue_key"] = (resolve_venue(d.get("venue_name"))
                              or d["source"])
        elif d["source"] in PROMOTER_SOURCES:
            # None marks a promoter row with no displayable venue: either a
            # listing-level placeholder awaiting detail enrichment
            # (venue_name None) or a venue string not in venues.CANONICAL.
            # Exporting those made the promoter itself show up as a venue.
            d["venue_key"] = resolve_venue(d.get("venue_name"))
        else:
            d["venue_key"] = d["source"]
            by_date_venue.setdefault(
                (d.get("start_date"), d["source"]), []).append(d)

    wins = _festival_windows(events)
    out: list[dict] = []
    #: promoter rows already kept, by (start_date, venue_key) — the fold
    #: targets for later promoter rows describing the same show (R6):
    #: two promoters co-listing one gap-venue show, or one promoter's
    #: two detail pages for a single performance.
    promo_kept: dict[tuple[str, str], list[dict]] = {}
    merged = fest_folded = promo_folded = unresolved = 0
    for d in events:
        if d["source"] == FESTIVAL_SOURCE:
            out.append(d)
            continue
        if wins and _is_festival_duplicate(d, wins):
            fest_folded += 1
            continue
        if d["source"] not in PROMOTER_SOURCES:
            out.append(d)
            continue
        if not d["venue_key"]:
            unresolved += 1
            continue
        candidates = by_date_venue.get((d.get("start_date"), d["venue_key"]))
        hit = next((c for c in candidates or []
                    if _artist_overlap(d, c)), None)
        if hit is not None:
            _merge(hit, d)
            merged += 1
            continue
        key = (d.get("start_date"), d["venue_key"])
        prior = next(
            (k for k in promo_kept.get(key, [])
             if _times_compatible(k, d)
             and (_titles_overlap(k, d) if k["source"] == d["source"]
                  else _artist_overlap(d, k))),
            None)
        if prior is not None:
            _merge(prior, d)
            promo_folded += 1
            continue
        out.append(d)
        promo_kept.setdefault(key, []).append(d)
    if merged or fest_folded or promo_folded or unresolved:
        print(f"promoter merge: folded {merged} duplicate rows into venue "
              f"records, {fest_folded} into festival records, "
              f"{promo_folded} promoter-vs-promoter; skipped "
              f"{unresolved} promoter rows with no resolved venue")
    return out
