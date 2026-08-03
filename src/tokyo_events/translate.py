"""Machine-translated English titles at export (roadmap R12 / FE-5).

models.py reserved this path from day one: title_en "may be machine-
translated later and flagged as such". Like genres, this runs at EXPORT
time only — it never feeds content_hash — and caches one verdict per
event id (title_en_cache), so each event is translated once. Every
MT-filled title is marked ``title_en_mt: True`` and the frontend renders
a small MT badge; scraper-provided English titles (the Mori pair) are
never touched and never flagged.

Without ANTHROPIC_API_KEY the pass applies cached verdicts and queues
nothing — the site simply stays JA-titled where no cache exists.
Titles that are already mostly Latin text pass through untranslated
(they read fine in EN mode as-is; no cache row, no badge).
"""

from __future__ import annotations

import json
import os
import re

_LLM_MODEL = "claude-haiku-4-5"
_BATCH = 20
_MAX_PER_RUN = 200

#: titles already ~readable without Japanese: >= 70% of their
#: non-space characters are ASCII
_ASCII_SHARE = 0.7

_PROMPT = """You are translating Japanese live-event titles for an \
English-language Tokyo events listing.
Rules:
- Keep artist names, tour titles and proper nouns as-is; romanize a \
Japanese act name only when it has no obvious official Latin spelling.
- Translate descriptive words (公演 -> concert/performance, 生誕祭 -> \
birthday live, 昼公演/夜公演 -> day show/evening show, 展 -> exhibition, \
単独公演 -> solo show).
- NEVER add information that is not in the title. Keep it short.
Titles (JSON): {items}
Answer with ONLY a JSON object mapping each id to its English title."""


def _mostly_ascii(title: str) -> bool:
    chars = [c for c in title if not c.isspace()]
    if not chars:
        return True
    ascii_n = sum(1 for c in chars if c.isascii())
    return ascii_n / len(chars) >= _ASCII_SHARE


def ensure_cache_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS title_en_cache ("
                 "event_id TEXT PRIMARY KEY, title_en TEXT NOT NULL, "
                 "tagger TEXT NOT NULL)")
    conn.commit()


def _llm_call(api_key: str, batch: list[dict]) -> dict[str, str]:
    import requests
    payload = {
        "model": _LLM_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": _PROMPT.format(
            items=json.dumps(
                [{"id": d["id"], "title": (d.get("title_ja") or "")[:160]}
                 for d in batch], ensure_ascii=False))}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=payload, timeout=60)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    out = {}
    for k, v in json.loads(m.group(0)).items():
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:200]
    return out


def apply_title_en(conn, events: list[dict]) -> None:
    """Fill d['title_en'] + d['title_en_mt'] for exported events lacking
    a real English title. Never raises — translation must not break
    export."""
    try:
        _apply(conn, events)
    except Exception as e:                      # pragma: no cover
        print(f"title translation failed ({e}); exporting without MT")


def _apply(conn, events: list[dict]) -> None:
    ensure_cache_table(conn)
    cache = {row[0]: row[1] for row in
             conn.execute("SELECT event_id, title_en FROM title_en_cache")}

    todo: list[dict] = []
    for d in events:
        if d.get("title_en"):          # scraper-provided English: leave be
            continue
        title = d.get("title_ja")
        if not title or _mostly_ascii(title):
            continue
        if d["id"] in cache:
            d["title_en"] = cache[d["id"]]
            d["title_en_mt"] = True
            continue
        todo.append(d)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not todo:
        return
    for i in range(0, min(len(todo), _MAX_PER_RUN), _BATCH):
        batch = todo[i:i + _BATCH]
        try:
            verdicts = _llm_call(api_key, batch)
        except Exception as e:          # LLM problems never break export
            print(f"title MT batch failed ({e}); keeping JA titles")
            break
        for d in batch:
            en = verdicts.get(d["id"])
            if en:
                d["title_en"] = en
                d["title_en_mt"] = True
                conn.execute(
                    "INSERT OR REPLACE INTO title_en_cache VALUES (?,?,?)",
                    (d["id"], en, _LLM_MODEL))
    conn.commit()
