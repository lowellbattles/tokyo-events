import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.artists import apply_artists, clean_artist, norm_key
from tokyo_events.db import EventStore


def test_norm_key_matches_frontend_norm():
    # frontend: NFKC + lowercase + whitespace collapse
    assert norm_key("ＡＢＣ　Ｄｅｆ") == "abc def"
    assert norm_key("  King  Gnu ") == "king gnu"
    assert norm_key("ヨルシカ") == "ヨルシカ"


def test_clean_artist_strips_roles_and_placeholders():
    assert clean_artist("O.A.: バウンディ") == "バウンディ"
    assert clean_artist("GUEST：androp") == "androp"
    assert clean_artist("and more") is None
    assert clean_artist("ほか") is None
    assert clean_artist("w/ ハンブレッダーズ") == "ハンブレッダーズ"
    assert clean_artist("X") is None                     # too short


def _ev(eid, title, lineup=()):
    return {"id": eid, "title_ja": title, "title_en": None,
            "lineup": list(lineup)}


def test_apply_artists_lineup_titles_and_guards(tmp_path):
    store = EventStore(tmp_path / "a.db")
    events = [
        _ev("e1", "夏の対バン", ["androp", "ヨルシカ"]),
        _ev("e2", "androp one-man tour 2099"),        # title match (pass 2)
        _ev("e3", "LOVEDRIVE release party", ["LOVE"]),
        _ev("e4", "ヨルシカな夜"),                     # JA substring match
        _ev("e5", "some other show"),
    ]
    apply_artists(store.conn, events)

    assert events[0]["artists"] == ["androp", "ヨルシカ"]
    # e2 linked via guarded ASCII title match
    assert events[1]["artists"] == ["androp"]
    # ASCII word boundary: "LOVE" must NOT match inside "LOVEDRIVE"
    # (e3 has LOVE via its own lineup, not via the title)
    assert events[3]["artists"] == ["ヨルシカ"]
    assert events[4]["artists"] == []

    rows = {r["norm_key"]: r["name"] for r in
            store.conn.execute("SELECT name, norm_key FROM artists")}
    assert set(rows) == {"androp", "ヨルシカ", "love"}
    n_links = store.conn.execute(
        "SELECT COUNT(*) FROM event_artists").fetchone()[0]
    assert n_links == 5      # e1×2, e2×1, e3×1, e4×1


def test_apply_artists_rebuild_is_idempotent(tmp_path):
    store = EventStore(tmp_path / "b.db")
    events = [_ev("e1", "show", ["androp"])]
    apply_artists(store.conn, events)
    apply_artists(store.conn, events)
    assert store.conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) FROM event_artists").fetchone()[0] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) FROM artist_aliases").fetchone()[0] == 1


def test_display_name_voting(tmp_path):
    store = EventStore(tmp_path / "c.db")
    events = [
        _ev("e1", "a", ["KING GNU"]),
        _ev("e2", "b", ["King Gnu"]),
        _ev("e3", "c", ["King Gnu"]),
    ]
    apply_artists(store.conn, events)
    row = store.conn.execute("SELECT name FROM artists").fetchone()
    assert row["name"] == "King Gnu"            # majority form wins
    # case variants share one norm_key -> one alias row (schema UNIQUE);
    # the alias table's job is JA/EN merges, which have distinct keys
    n_alias = store.conn.execute(
        "SELECT COUNT(*) FROM artist_aliases").fetchone()[0]
    assert n_alias == 1
