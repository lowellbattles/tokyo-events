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
   the site coverage no venue scraper can.

Like genres/artists, this runs at export only — the DB keeps pure
per-source facts and alias updates re-resolve without re-scraping.
"""

from __future__ import annotations

from .artists import norm_key
from .venues import resolve_venue

PROMOTER_SOURCES = {"sogo_tokyo", "creativeman"}


def _artist_overlap(promo: dict, venue_ev: dict) -> bool:
    """True when the promoter row and the venue row plausibly describe the
    same show: any promoter act appears in the venue event's title/lineup
    (or vice versa)."""
    v_hay = norm_key(" ".join(filter(None, (
        venue_ev.get("title_ja"), venue_ev.get("title_en"),
        *(venue_ev.get("lineup") or [])))))
    p_names = [n for n in (promo.get("lineup") or []) if n]
    p_names += [t for t in (promo.get("title_ja"), promo.get("title_en")) if t]
    p_hay = norm_key(" ".join(p_names))
    for name in p_names:
        n = norm_key(name)
        if len(n) >= 3 and n in v_hay:
            return True
    for name in (venue_ev.get("lineup") or []):
        n = norm_key(name)
        if len(n) >= 3 and n in p_hay:
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


def _apply(events: list[dict]) -> list[dict]:
    by_date_venue: dict[tuple[str, str], list[dict]] = {}
    for d in events:
        if d["source"] in PROMOTER_SOURCES:
            d["venue_key"] = resolve_venue(d.get("venue_name")) or d["source"]
        else:
            d["venue_key"] = d["source"]
            by_date_venue.setdefault(
                (d.get("start_date"), d["source"]), []).append(d)

    out: list[dict] = []
    merged = 0
    for d in events:
        if d["source"] not in PROMOTER_SOURCES:
            out.append(d)
            continue
        candidates = by_date_venue.get((d.get("start_date"), d["venue_key"]))
        hit = next((c for c in candidates or []
                    if _artist_overlap(d, c)), None)
        if hit is not None:
            _merge(hit, d)
            merged += 1
        else:
            out.append(d)
    if merged:
        print(f"promoter merge: folded {merged} duplicate rows into "
              f"venue records")
    return out
