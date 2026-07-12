"""Storage layer: SQLite with a staging/review workflow.

Lifecycle of a scraped event:
  1. Scraper yields an Event -> upsert() stores it with status=pending
     (or the source's default, e.g. AUTO for trusted sources).
  2. If an already-stored event's content_hash changed, it is updated and
     flipped back to pending so a human re-checks it.
  3. Admin approves/rejects via CLI (later: web admin).
  4. The public site queries only approved/auto events.

Artist tables are created now (cheap) but populated in the artist
cross-referencing phase: events.lineup -> normalized artist keys ->
alias merges in review -> artist pages.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .models import Event, ReviewStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,          -- Event.dedupe_key()
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    data          TEXT NOT NULL,             -- full Event as JSON
    start_date    TEXT,
    end_date      TEXT,
    category      TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date   ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_cat    ON events(category);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    found       INTEGER DEFAULT 0,
    new         INTEGER DEFAULT 0,
    changed     INTEGER DEFAULT 0,
    details_fetched INTEGER DEFAULT 0,
    error       TEXT
);

-- Artist cross-referencing (populated in a later phase)
CREATE TABLE IF NOT EXISTS artists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,          -- display name
    norm_key   TEXT NOT NULL UNIQUE    -- NFKC-lowercased dedupe key
);
CREATE TABLE IF NOT EXISTS artist_aliases (
    artist_id  INTEGER NOT NULL REFERENCES artists(id),
    alias      TEXT NOT NULL,
    norm_key   TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS event_artists (
    event_id   TEXT NOT NULL REFERENCES events(id),
    artist_id  INTEGER NOT NULL REFERENCES artists(id),
    PRIMARY KEY (event_id, artist_id)
);
"""


#: fields the detail pass fills; a listing re-parse that lacks them must not
#: wipe previously enriched values (a listing-only run would otherwise
#: clobber them AND make the barer version the stored state, so the event
#: never re-enriches).
DETAIL_FILL_FIELDS = ("open_time", "start_time", "price_text", "price_min",
                      "is_free", "ticket_url", "ticket_links")


class EventStore:
    def __init__(self, path: str | Path = "events.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- ingestion ---------------------------------------------------------
    def upsert(self, ev: Event, default_status: ReviewStatus = ReviewStatus.PENDING
               ) -> str:
        """Insert or update. Returns 'new' | 'changed' | 'unchanged'.

        On update, detail-pass fields the incoming listing event lacks are
        merged back in from the stored version (mutating ev), so transient
        listing gaps neither count as changes nor erase enrichment."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        eid, chash = ev.dedupe_key(), ev.content_hash()
        row = self.conn.execute(
            "SELECT content_hash, status, data FROM events WHERE id=?", (eid,)
        ).fetchone()

        if row is not None and row["content_hash"] != chash:
            stored = json.loads(row["data"])
            for f in DETAIL_FILL_FIELDS:
                if getattr(ev, f) in (None, []) and stored.get(f) not in (None, []):
                    setattr(ev, f, stored[f])
            chash = ev.content_hash()

        if row is None:
            self.conn.execute(
                "INSERT INTO events (id, source, source_url, content_hash, "
                "status, data, start_date, end_date, category, first_seen, "
                "last_seen, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, ev.source, ev.source_url, chash, default_status.value,
                 json.dumps(ev.to_json(), ensure_ascii=False),
                 ev.start_date, ev.end_date, ev.category.value, now, now, now),
            )
            self.conn.commit()
            return "new"

        if row["content_hash"] == chash:
            self.conn.execute(
                "UPDATE events SET last_seen=? WHERE id=?", (now, eid))
            self.conn.commit()
            return "unchanged"

        new_status = (row["status"] if default_status == ReviewStatus.AUTO
                      else ReviewStatus.PENDING.value)
        self.conn.execute(
            "UPDATE events SET content_hash=?, data=?, start_date=?, "
            "end_date=?, category=?, status=?, last_seen=?, updated_at=? "
            "WHERE id=?",
            (chash, json.dumps(ev.to_json(), ensure_ascii=False),
             ev.start_date, ev.end_date, ev.category.value,
             new_status, now, now, eid),
        )
        self.conn.commit()
        return "changed"

    # --- review ------------------------------------------------------------
    def set_status(self, event_id: str, status: ReviewStatus) -> None:
        self.conn.execute("UPDATE events SET status=?, updated_at=? WHERE id=?",
                          (status.value, dt.datetime.now().isoformat(), event_id))
        self.conn.commit()

    # --- queries -----------------------------------------------------------
    def list_events(self, status: str | None = None, category: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    public_only: bool = False) -> list[dict]:
        q, args = "SELECT id, status, data FROM events WHERE 1=1", []
        if public_only:
            q += " AND status IN ('approved','auto')"
        if status:
            q += " AND status=?"; args.append(status)
        if category:
            q += " AND category=?"; args.append(category)
        if date_from:
            q += " AND start_date>=?"; args.append(date_from)
        if date_to:
            q += " AND start_date<=?"; args.append(date_to)
        q += " ORDER BY start_date"
        out = []
        for row in self.conn.execute(q, args):
            d = json.loads(row["data"])
            d["id"], d["status"] = row["id"], row["status"]
            out.append(d)
        return out

    def events_needing_detail(self, source: str, exclude_urls: set[str],
                              limit: int) -> list[Event]:
        """Upcoming events of a source whose stored data still lacks detail
        fields (ticket links / start time / price) — the backlog the detail
        pass drains across runs even when listings are unchanged."""
        if limit <= 0:
            return []
        out: list[Event] = []
        rows = self.conn.execute(
            "SELECT data FROM events WHERE source=? AND status!='rejected' "
            "AND start_date>=date('now') ORDER BY start_date", (source,))
        for row in rows:
            d = json.loads(row["data"])
            if d.get("source_url") in exclude_urls:
                continue
            if (not d.get("ticket_links") or d.get("start_time") is None
                    or d.get("price_min") is None):
                out.append(Event.from_json(d))
                if len(out) >= limit:
                    break
        return out

    def source_health(self) -> list[dict]:
        """Latest scrape_runs row per source, for status display."""
        rows = self.conn.execute(
            "SELECT source, started_at, found, new, changed, error "
            "FROM scrape_runs WHERE id IN "
            "(SELECT MAX(id) FROM scrape_runs GROUP BY source) "
            "ORDER BY source").fetchall()
        return [dict(r) for r in rows]

    def export_public_json(self, path: str | Path) -> int:
        """Dump approved events + source health as the frontend feed."""
        from .genres import apply_genres
        from .artists import apply_artists
        events = self.list_events(public_only=True)
        apply_genres(self.conn, events)
        apply_artists(self.conn, events)
        Path(path).write_text(
            json.dumps({"generated_at": dt.datetime.now().isoformat(),
                        "sources": self.source_health(),
                        "events": events}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return len(events)
