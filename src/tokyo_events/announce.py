"""'Just announced' dates for the feed (the frontend's 新着 view).

An event's announcement date is the JST day we FIRST saw it (the events
table's first_seen) — facts we observed, not a guess at the promoter's
press date. Our first sighting only means "announced" when the source
was already watching that far ahead. Two situations produce a flood of
first sightings that are NOT new announcements, and are excluded:

- a source's first-ever run (onboarding: everything is "new" to us),
  derived automatically from the earliest first_seen per source;
- a deliberate coverage change — a wider month horizon, a parser fix
  that recovers dropped shows, an identity (source_url) migration —
  recorded by hand in BACKFILL below: sightings first seen on or before
  that JST date are backfill. Add an entry whenever such a change ships;
  the date is the first scheduled run that includes it.

A backfill sighting is "unknown when announced", which is not the same
as "new": when the promoter merge folds several sightings of one show,
the EARLIEST wins and an unknown sighting (the "" sentinel) beats every
date, so the show is never flagged.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from .scrapers.textutils import JST

#: source_id -> JST date (inclusive) up to which first sightings are
#: backfill from a coverage change, not announcements.
BACKFILL: dict[str, str] = {
    # 12-month promoter horizon + creativeman tour cap/microsites
    # (commit 405be97; first scheduled run 2026-09-25 07:00 JST)
    "creativeman": "2026-09-25",
    "smash_jpn": "2026-09-25",
    "sogo_tokyo": "2026-09-25",
    "disk_garage": "2026-09-25",
    # redesigned calendar: every show re-keyed to exec/<id> URLs
    "cotton_club": "2026-09-25",
}

#: sighting whose announcement date is unknown — sorts before any date
UNKNOWN = ""


def jst_date(ts: str | None) -> str | None:
    """UTC ISO timestamp (as stored) -> JST calendar date, or None."""
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(JST).date().isoformat()


def sighting_date(source: str, first_seen: str | None,
                  source_start: str | None,
                  backfill: dict[str, str] | None = None) -> str:
    """JST announcement date of one sighting, or UNKNOWN for backfill."""
    backfill = BACKFILL if backfill is None else backfill
    day = jst_date(first_seen)
    if day is None or day == source_start:
        return UNKNOWN
    cutoff = backfill.get(source)
    if cutoff and day <= cutoff:
        return UNKNOWN
    return day


def attach_first_seen(conn: sqlite3.Connection, events: list[dict]) -> None:
    """Stamp each exported event dict with its sighting date under
    "first_seen" (pre-merge; promoters._merge keeps the earliest)."""
    seen: dict[str, str] = {}
    starts: dict[str, str] = {}
    for eid, source, first in conn.execute(
            "SELECT id, source, first_seen FROM events"):
        seen[eid] = first
        day = jst_date(first)
        if day and (source not in starts or day < starts[source]):
            starts[source] = day
    for d in events:
        d["first_seen"] = sighting_date(
            d["source"], seen.get(d.get("id")), starts.get(d["source"]))


def finalize(events: list[dict]) -> None:
    """Turn the merged sighting date into the public `announced` field
    (absent when unknown)."""
    for d in events:
        day = d.pop("first_seen", None)
        if day:
            d["announced"] = day
