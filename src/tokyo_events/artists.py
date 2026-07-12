"""Artist cross-referencing: populate artists / artist_aliases /
event_artists from scraped data, and attach canonical artist names to
exported events.

Runs at EXPORT time (like genres) so it never feeds content_hash and can
be rebuilt from scratch on every export — the tables are a derived index,
not source data.

Extraction, two passes over the public event set:
  1. lineup names (scrapers already parse support acts etc. into lineup)
     define the artist universe. Raw display variants are kept as aliases
     of their normalized key; JA vs EN spellings of the same act stay
     separate artists until merged (alias table exists for that phase).
  2. title matching: events whose title contains a known artist (the same
     NFKC substring logic the frontend uses, with guards against short
     false-positive keys) get linked too — this is what makes "this
     artist's other upcoming shows" work for one-man shows whose lineup
     the venue never lists.
"""

from __future__ import annotations

import re
import unicodedata

#: strip leading role markers venues prepend to lineup entries
_ROLE_PREFIX_RE = re.compile(
    r"^(?:O\.?A\.?|ゲスト|GUEST|w/|with|feat\.?|FEAT\.?)[:：\s]+", re.I)
#: entries that are placeholders, not artists
_PLACEHOLDER_RE = re.compile(
    r"^(?:and\s+more|more|ほか|他|TBA|TBD|COMING\s+SOON|未定|調整中)[.…!！]*$",
    re.I)

_WS_RE = re.compile(r"\s+")


def norm_key(name: str) -> str:
    """NFKC + casefold + whitespace-collapse key, mirroring the frontend's
    norm() so client and DB agree on identity."""
    s = unicodedata.normalize("NFKC", str(name or ""))
    return _WS_RE.sub(" ", s).strip().casefold()


def clean_artist(raw: str) -> str | None:
    """Normalize a lineup entry to a display name, or None if it isn't
    actually an artist name."""
    s = _WS_RE.sub(" ", str(raw or "")).strip(" 　・/／|,、")
    s = _ROLE_PREFIX_RE.sub("", s).strip()
    if not (2 <= len(s) <= 80) or _PLACEHOLDER_RE.match(s):
        return None
    return s


def _title_match_re(key: str) -> re.Pattern | None:
    """A guarded pattern for finding an artist key inside a normalized
    title. ASCII-only keys need word boundaries; short keys are too
    false-positive-prone to match at all."""
    if re.fullmatch(r"[\x20-\x7e]+", key):
        if len(key) < 4:
            return None
        return re.compile(rf"(?<![0-9a-z]){re.escape(key)}(?![0-9a-z])")
    if len(key) < 3:
        return None
    return re.compile(re.escape(key))


def apply_artists(conn, events: list[dict]) -> None:
    """Rebuild the artist tables from `events` and set d["artists"] on
    each exported event (canonical display names). Never raises — the
    artist index must not break export."""
    try:
        _apply(conn, events)
    except Exception as e:                      # pragma: no cover
        print(f"artist indexing failed ({e}); exporting without artists")
        for d in events:
            d.setdefault("artists", [])


def _apply(conn, events: list[dict]) -> None:
    # -- pass 1: artist universe from lineups ----------------------------
    # display-name votes per norm key, and event links
    votes: dict[str, dict[str, int]] = {}
    links: dict[str, set[str]] = {}             # norm key -> event ids
    aliases: dict[str, set[str]] = {}           # norm key -> raw variants
    for d in events:
        for raw in d.get("lineup") or []:
            name = clean_artist(raw)
            if not name:
                continue
            key = norm_key(name)
            if not key:
                continue
            votes.setdefault(key, {})
            votes[key][name] = votes[key].get(name, 0) + 1
            aliases.setdefault(key, set()).add(name)
            links.setdefault(key, set()).add(d["id"])

    # -- pass 2: title matching against the known universe ---------------
    matchers = {k: rx for k in votes
                if (rx := _title_match_re(k)) is not None}
    for d in events:
        hay = norm_key(f"{d.get('title_ja') or ''} {d.get('title_en') or ''}")
        if not hay:
            continue
        for key, rx in matchers.items():
            if d["id"] not in links[key] and rx.search(hay):
                links[key].add(d["id"])

    # -- rebuild tables (derived index: wipe links, upsert artists) ------
    conn.execute("DELETE FROM event_artists")
    canonical: dict[str, str] = {}
    for key, forms in votes.items():
        display = max(forms.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        canonical[key] = display
        row = conn.execute("SELECT id, name FROM artists WHERE norm_key=?",
                           (key,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO artists (name, norm_key) VALUES (?,?)",
                (display, key))
            artist_id = cur.lastrowid
        else:
            artist_id = row["id"]
            if row["name"] != display:
                conn.execute("UPDATE artists SET name=? WHERE id=?",
                             (display, artist_id))
        for alias in aliases[key]:
            conn.execute(
                "INSERT OR IGNORE INTO artist_aliases "
                "(artist_id, alias, norm_key) VALUES (?,?,?)",
                (artist_id, alias, norm_key(alias)))
        conn.executemany(
            "INSERT OR IGNORE INTO event_artists (event_id, artist_id) "
            "VALUES (?,?)",
            [(eid, artist_id) for eid in links[key]])
    conn.commit()

    # -- attach canonical names to the exported events --------------------
    by_event: dict[str, list[str]] = {}
    for key, eids in links.items():
        for eid in eids:
            by_event.setdefault(eid, []).append(canonical[key])
    for d in events:
        d["artists"] = sorted(by_event.get(d["id"], []))
