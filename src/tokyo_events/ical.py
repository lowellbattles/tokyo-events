"""RFC 5545 (iCalendar) export.

Pure functions over the same event-dict shape the frontend consumes from
site/public.json (see site/index.html's data-contract comment and
db.py's export_public_json) -- no DB/pipeline imports, no filesystem
I/O. The integrator wires build_all()'s output into cli.py's export
flow and writes the returned {relative_path: ics_text} map to disk.

Design notes:
- UID reuses models.Event.dedupe_key()'s recipe (sha256("source|source_
  url") truncated to 16 hex chars) so a given source event always maps
  to the same calendar UID across runs. Recomputed here (not imported)
  because events arrive as plain dicts, not Event instances -- see
  CLAUDE.md's schema-changes rule: this mirrors models.py by
  convention, so if that recipe ever changes, update both.
- Facts only, same as the rest of the pipeline (CLAUDE.md rule 1): no
  descriptions/images, just title/date/venue/price/artists/link.
- Unknown durations are never fabricated: a timed event gets DTSTART
  only (no DTEND) -- we don't know how long the show runs.
- Only category "music" / "music_festival" events are calendar
  material for now (roadmap R26); art/festival/fireworks/flowers are
  out of scope for this pass.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

#: categories that count as "live music" for calendar purposes
_ICAL_CATEGORIES = ("music", "music_festival")

_PRODID = "-//tokyo-events//JP"


def _escape_text(s: str) -> str:
    """RFC 5545 3.3.11 TEXT escaping (backslash, semicolon, comma,
    newline). Order matters: backslash is escaped FIRST, and the
    newline->"\\n" substitution happens LAST, so the backslashes this
    function itself introduces are never re-escaped."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\n", "\\n")
    return s


def _fold_line(line: str) -> str:
    """Fold one unfolded content line into RFC 5545 75-octet physical
    lines (CRLF + single-space continuation). UTF-8 aware: a fold point
    is never placed inside a multi-byte character -- each chunk boundary
    is backed off to the start of a character before it is decoded."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    chunks = []
    start, n, limit = 0, len(data), 75
    while start < n:
        end = min(start + limit, n)
        while end < n and (data[end] & 0xC0) == 0x80:  # UTF-8 continuation byte
            end -= 1
        chunks.append(data[start:end].decode("utf-8"))
        start = end
        limit = 74  # continuation line reserves 1 octet for the leading space
    return "\r\n ".join(chunks)


def _uid(ev: dict) -> str:
    """Same recipe as models.Event.dedupe_key(); reimplemented inline
    since ical.py takes plain dicts, not Event instances (see module
    docstring)."""
    raw = f"{ev.get('source', '')}|{ev.get('source_url', '')}".encode()
    return hashlib.sha256(raw).hexdigest()[:16] + "@tokyo-events"


def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_ymd(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def _sort_key(ev: dict) -> tuple:
    return (ev.get("start_date") or "", ev.get("start_time") or "", _uid(ev))


def _vevent_lines(ev: dict, now: datetime) -> list[str] | None:
    """Build one VEVENT's unfolded property lines, or None if the event
    can't carry a VEVENT (no start_date -> no valid DTSTART)."""
    start_date = ev.get("start_date")
    if not start_date:
        return None

    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(ev)}",
        f"DTSTAMP:{_fmt_utc(now)}",
    ]

    start_time = ev.get("start_time")
    if start_time:
        hh, mm = start_time.split(":")
        y, m, d = start_date.split("-")
        lines.append(f"DTSTART;TZID=Asia/Tokyo:{y}{m}{d}T{hh}{mm}00")
        # duration unknown -- omit DTEND rather than fabricate one
    else:
        start = _parse_ymd(start_date)
        end_exclusive = (_parse_ymd(ev.get("end_date") or start_date)
                          + timedelta(days=1))
        lines.append(f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{end_exclusive.strftime('%Y%m%d')}")

    title = ev.get("title_ja") or ev.get("title_en") or ""
    if ev.get("is_sold_out"):
        title = f"{title} [SOLD OUT]"
    lines.append(f"SUMMARY:{_escape_text(title)}")

    venue_name = ev.get("venue_name")
    if venue_name:
        lines.append(f"LOCATION:{_escape_text(venue_name)}")

    desc_parts = []
    artists = ev.get("artists") or []
    if artists:
        desc_parts.append(", ".join(artists))
    if ev.get("price_min"):
        desc_parts.append(f"¥{ev['price_min']:,}~")
    elif ev.get("is_free"):
        desc_parts.append("Free")
    if desc_parts:
        lines.append(f"DESCRIPTION:{_escape_text(chr(10).join(desc_parts))}")

    source_url = ev.get("source_url") or ""
    if source_url.startswith("http://") or source_url.startswith("https://"):
        lines.append(f"URL:{source_url}")  # URI value type -- not TEXT-escaped

    lines.append("END:VEVENT")
    return lines


def build_calendar(events: list[dict], name: str,
                    now: datetime | None = None) -> str:
    """Return a complete RFC 5545 VCALENDAR text for `events`.

    Events without start_date are skipped (no valid DTSTART possible).
    Output is deterministic: events are sorted by
    (start_date, start_time or '', UID) regardless of input order.
    `now` pins DTSTAMP (all VEVENTs in one call share one timestamp);
    defaults to the current UTC time.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is not None:
        now = now.astimezone(timezone.utc)

    built = []
    for ev in events:
        vlines = _vevent_lines(ev, now)
        if vlines is not None:
            built.append((_sort_key(ev), vlines))
    built.sort(key=lambda pair: pair[0])

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape_text(name)}",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Tokyo",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900",
        "TZOFFSETTO:+0900",
        "TZNAME:JST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for _, vlines in built:
        lines.extend(vlines)
    lines.append("END:VCALENDAR")

    folded = [_fold_line(l) for l in lines]
    return "\r\n".join(folded) + "\r\n"


def build_all(events: list[dict], now: datetime | None = None) -> dict[str, str]:
    """Return {relative_path: ics_text}: "events.ics" for all upcoming
    music + music_festival events, plus "ical/<venue_key>.ics" per
    venue_key with >=1 such event.

    The caller has already filtered `events` to upcoming (roadmap R3 /
    db.export_public_json's own window) -- this does not re-check dates.
    Events without start_date are dropped (mirrors build_calendar's own
    per-event guard). venue_key falls back to source when absent,
    mirroring the frontend's `venue_key || source` venue identity
    (site/index.html's venueIdOf).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    relevant = [ev for ev in events
                if ev.get("category") in _ICAL_CATEGORIES
                and ev.get("start_date")]

    out: dict[str, str] = {
        "events.ics": build_calendar(relevant, "Tokyo Events", now=now),
    }

    by_venue: dict[str, list[dict]] = {}
    for ev in relevant:
        vkey = ev.get("venue_key") or ev.get("source")
        by_venue.setdefault(vkey, []).append(ev)

    for vkey, evs in by_venue.items():
        vname = next((e.get("venue_name") for e in evs if e.get("venue_name")),
                      vkey)
        out[f"ical/{vkey}.ics"] = build_calendar(
            evs, f"Tokyo Events — {vname}", now=now)

    return out
