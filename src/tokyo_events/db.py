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
from .scrapers.textutils import jst_today

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
    error       TEXT,
    skipped_venues TEXT               -- JSON array: raw venue strings the
                                      -- scraper saw but could not resolve
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

#: internal / never-rendered fields stripped from the public feed
#: (roadmap R3): they stay in events.db, so re-add one deliberately if
#: the frontend grows a feature that needs it (map -> lat/lng, price
#: tiers -> price_text). index.html's data-contract comment mirrors
#: this list — the feed and frontend move together (CLAUDE.md rule 5).
EXPORT_DROP_FIELDS = ("price_text", "id", "status", "ticket_url",
                      "lat", "lng", "tags")


class EventStore:
    def __init__(self, path: str | Path = "events.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations for DBs created before a schema grew.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so the
        committed events.db needs ALTERs for columns added later."""
        cols = {r["name"] for r in
                self.conn.execute("PRAGMA table_info(scrape_runs)")}
        if "skipped_venues" not in cols:
            self.conn.execute(
                "ALTER TABLE scrape_runs ADD COLUMN skipped_venues TEXT")
            self.conn.commit()

    # --- ingestion ---------------------------------------------------------
    def upsert(self, ev: Event, default_status: ReviewStatus = ReviewStatus.PENDING
               ) -> str:
        """Insert or update. Returns 'new' | 'changed' | 'unchanged'.

        On update, detail-pass fields the incoming listing event lacks are
        merged back in from the stored version (mutating ev), so transient
        listing gaps neither count as changes nor erase enrichment."""
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        eid, chash = ev.dedupe_key(), ev.content_hash()
        row = self.conn.execute(
            "SELECT content_hash, status, data FROM events WHERE id=?", (eid,)
        ).fetchone()

        if row is not None and row["content_hash"] != chash:
            stored = json.loads(row["data"])
            for f in DETAIL_FILL_FIELDS:
                if getattr(ev, f) in (None, []) and stored.get(f) not in (None, []):
                    setattr(ev, f, stored[f])
            # Sold-out latch (R9): many venues only mark SOLD OUT on the
            # event's own page, so every listing re-parse said False and
            # the sweep flipped it back True — two spurious "changed"
            # writes plus a wasted detail fetch per event per day.
            # Sticky until the event passes (the sweep never un-marks
            # either); a venue genuinely re-opening sales is the rare
            # case we accept losing.
            if stored.get("is_sold_out") and not ev.is_sold_out:
                ev.is_sold_out = True
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

        # A human's reject is a curation decision — content churn (sold-out
        # flips, time corrections) must not resurface the event for review.
        if row["status"] == ReviewStatus.REJECTED.value:
            new_status = ReviewStatus.REJECTED.value
        elif default_status == ReviewStatus.AUTO:
            new_status = row["status"]
        else:
            new_status = ReviewStatus.PENDING.value
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
                          (status.value,
                           dt.datetime.now(dt.timezone.utc).isoformat(
                               timespec="seconds"), event_id))
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
        """Upcoming events of a source that were never detail-enriched at
        all (no start time AND no price AND no ticket links) — the backlog
        the detail pass drains across runs even when listings are
        unchanged. Deliberately conservative: an event where SOME detail
        fields stuck is not retried, because many venues simply never
        publish the rest (retrying those would refetch the same pages
        every day forever)."""
        if limit <= 0:
            return []
        out: list[Event] = []
        today = jst_today().isoformat()
        rows = self.conn.execute(
            "SELECT data FROM events WHERE source=? AND status!='rejected' "
            "AND category!='other' "         # don't spend fetches on junk
            "ORDER BY start_date", (source,))
        for row in rows:
            d = json.loads(row["data"])
            # still running or upcoming: exhibitions (date RANGES) stay
            # enrichable mid-run — their start_date is long past
            if (d.get("end_date") or d.get("start_date") or "") < today:
                continue
            if d.get("source_url") in exclude_urls:
                continue
            if (not d.get("ticket_links") and d.get("start_time") is None
                    and d.get("price_min") is None
                    and d.get("is_free") is None):
                # is_free counts as enrichment: a free museum show has no
                # price to fetch, and retrying it would refetch daily forever
                out.append(Event.from_json(d))
                if len(out) >= limit:
                    break
        return out

    def events_for_soldout_sweep(self, source: str, exclude_urls: set[str],
                                 limit: int, window_days: int = 10
                                 ) -> list[Event]:
        """Soon-upcoming events not yet marked sold out — candidates for a
        detail-page re-check. Sold-out marks usually appear on the venue's
        event page well after our one-time enrichment, so near-term shows
        get re-visited with whatever detail budget is left over."""
        if limit <= 0:
            return []
        out: list[Event] = []
        # bound params, not SQL date('now'): sqlite's clock is the
        # runner's (UTC) — the window must be JST like everything else
        today = jst_today()
        rows = self.conn.execute(
            "SELECT data FROM events WHERE source=? AND status!='rejected' "
            "AND category='music' AND start_date>=? "
            "AND start_date<=? ORDER BY start_date",
            (source, today.isoformat(),
             (today + dt.timedelta(days=window_days)).isoformat()))
        for row in rows:
            d = json.loads(row["data"])
            if d.get("is_sold_out") or d.get("source_url") in exclude_urls:
                continue
            out.append(Event.from_json(d))
            if len(out) >= limit:
                break
        return out

    def stale_upcoming(self, days: int = 3) -> list[dict]:
        """Approved/auto events still upcoming or running (JST) whose
        last_seen is older than `days` days — their source stopped
        listing them (possible CANCELLATION) or they sit beyond its
        month-walk window (harmless; re-freshens when the window
        arrives). Report-only (R8): nothing is auto-hidden, a human
        checks the source page and rejects confirmed cancellations.
        Callers gate on source health — the pipeline only attaches
        these to sources whose run just succeeded."""
        today = jst_today().isoformat()
        # substr(…,1,19) tolerates the historical mix of naive and
        # tz-aware timestamps in last_seen; 9h of skew is nothing at a
        # multi-day threshold
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=days)).isoformat(timespec="seconds")[:19]
        out = []
        for r in self.conn.execute(
                "SELECT id, source, start_date, last_seen, data FROM events "
                "WHERE status IN ('approved','auto') "
                "AND COALESCE(end_date, start_date) >= ? "
                "AND substr(last_seen, 1, 19) < ? "
                "ORDER BY start_date", (today, cutoff)):
            d = json.loads(r["data"])
            out.append({"id": r["id"], "source": r["source"],
                        "start_date": r["start_date"],
                        "last_seen": (r["last_seen"] or "")[:10],
                        "title": d.get("title_ja") or d.get("title_en") or ""})
        return out

    def source_health(self) -> list[dict]:
        """Latest scrape_runs row per source, for status display."""
        rows = self.conn.execute(
            "SELECT source, started_at, found, new, changed, error, "
            "skipped_venues FROM scrape_runs WHERE id IN "
            "(SELECT MAX(id) FROM scrape_runs GROUP BY source) "
            "ORDER BY source").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skipped_venues"] = (json.loads(d["skipped_venues"])
                                   if d["skipped_venues"] else [])
            out.append(d)
        return out

    def export_public_json(self, path: str | Path) -> int:
        """Dump upcoming approved events + source health as the frontend
        feed. The feed is a forward-looking view, not an archive
        (roadmap R3): events already over (JST), category "other" rows
        the UI never shows, undated events, and internal-only fields all
        stay in events.db but out of the payload."""
        from .genres import apply_genres
        from .artists import apply_artists
        from .promoters import apply_promoter_merge
        from .translate import apply_title_en
        today = jst_today().isoformat()
        events = [d for d in self.list_events(public_only=True)
                  if (d.get("end_date") or d.get("start_date") or "") >= today
                  and d.get("category") != "other"]
        events = apply_promoter_merge(events)   # before genre/artist passes
        apply_genres(self.conn, events)
        apply_artists(self.conn, events)
        apply_title_en(self.conn, events)       # MT titles, flagged (R12)
        for d in events:            # the passes above need id/status
            for f in EXPORT_DROP_FIELDS:
                d.pop(f, None)
        # frontend reads source/found/error; run internals and raw
        # skipped-venue strings are curation data, not feed data. Retired
        # sources keep their scrape_runs history but leave the footer —
        # their last row would show a stale error forever. Lazy import:
        # pipeline imports this module at load time.
        from .pipeline import SCRAPERS
        sources = [{"source": s["source"], "found": s["found"],
                    "error": s["error"]} for s in self.source_health()
                   if s["source"] in SCRAPERS]
        Path(path).write_text(
            json.dumps({"generated_at":
                        dt.datetime.now(dt.timezone.utc).isoformat(
                            timespec="seconds"),
                        "sources": sources,
                        "events": events},
                       ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        return len(events)
