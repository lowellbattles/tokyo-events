"""Machine-translated titles at export (roadmap R12): cached, flagged
title_en_mt, no-op without an API key, never touches scraper-provided
English titles, skips already-Latin titles."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events import translate  # noqa: E402
from tokyo_events.db import EventStore  # noqa: E402


def _store(tmp_path):
    return EventStore(tmp_path / "t.db")


def _ev(eid, title_ja, title_en=None):
    return {"id": eid, "title_ja": title_ja, "title_en": title_en}


def test_mostly_ascii_heuristic():
    assert translate._mostly_ascii("SUMMER SONIC 2026 TOKYO")
    assert translate._mostly_ascii("who killed paledusk TOUR")
    assert not translate._mostly_ascii("夏の対バン祭り")
    assert not translate._mostly_ascii("米津玄師 2026 TOUR")  # mixed, JA-heavy


def test_no_key_is_a_quiet_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = _store(tmp_path)
    events = [_ev("e1", "夏の対バン祭り")]
    translate.apply_title_en(store.conn, events)
    assert "title_en_mt" not in events[0]
    assert events[0]["title_en"] is None


def test_cached_verdict_applies_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = _store(tmp_path)
    translate.ensure_cache_table(store.conn)
    store.conn.execute("INSERT INTO title_en_cache VALUES (?,?,?)",
                       ("e1", "Summer Battle-of-the-Bands Fest",
                        translate._LLM_MODEL))
    store.conn.commit()
    events = [_ev("e1", "夏の対バン祭り")]
    translate.apply_title_en(store.conn, events)
    assert events[0]["title_en"] == "Summer Battle-of-the-Bands Fest"
    assert events[0]["title_en_mt"] is True


def test_scraper_english_title_never_flagged(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = _store(tmp_path)
    translate.ensure_cache_table(store.conn)
    store.conn.execute("INSERT INTO title_en_cache VALUES (?,?,?)",
                       ("e1", "SHOULD NOT APPLY", translate._LLM_MODEL))
    store.conn.commit()
    events = [_ev("e1", "モネ 睡蓮のとき", title_en="Monet: Water Lilies")]
    translate.apply_title_en(store.conn, events)
    assert events[0]["title_en"] == "Monet: Water Lilies"
    assert "title_en_mt" not in events[0]


def test_llm_verdicts_apply_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        translate, "_llm_call",
        lambda key, batch: {d["id"]: f"EN:{d['title_ja']}" for d in batch})
    store = _store(tmp_path)
    events = [_ev("e1", "夏の対バン祭り"), _ev("e2", "冬の単独公演")]
    translate.apply_title_en(store.conn, events)
    assert events[0]["title_en"] == "EN:夏の対バン祭り"
    assert events[0]["title_en_mt"] is True
    assert store.conn.execute(
        "SELECT COUNT(*) FROM title_en_cache").fetchone()[0] == 2
    # second export: cache hit, no LLM call needed
    monkeypatch.setattr(translate, "_llm_call",
                        lambda key, batch: (_ for _ in ()).throw(
                            AssertionError("should not be called")))
    events2 = [_ev("e1", "夏の対バン祭り")]
    translate.apply_title_en(store.conn, events2)
    assert events2[0]["title_en"] == "EN:夏の対バン祭り"
