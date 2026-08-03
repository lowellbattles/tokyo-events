"""Genre tagging: deterministic rules + optional LLM refinement.

Applied at EXPORT time, never at scrape/upsert time, so tags don't feed
content_hash and can't cause daily re-stage churn.

Precedence per event:
  1. genres set by the scraper itself (e.g. Billboard's own genre labels)
  2. cached LLM verdict (genre_cache table — each event is tagged once)
  3. rule matches (keywords / script detection below)
  4. venue-prior default (live houses -> j-rock)

LLM refinement runs only when ANTHROPIC_API_KEY is set (locally or as a
GitHub Actions secret) and only for events the rules weren't confident
about. Uses plain HTTPS via requests — no new dependency. Vocabulary is
locked to models.GENRES; the model may also answer "none".
"""

from __future__ import annotations

import json
import os
import re

from .models import ART_GENRES, GENRES

# --- rules ---------------------------------------------------------------

_RULES: list[tuple[str, re.Pattern]] = [
    ("idol", re.compile(
        r"アイドル|IDOL|生誕祭|定期公演|研究生|特典会|お披露目|"
        r"(?:AKB|SKE|NMB|HKT|NGT|STU)48|(?:乃木|櫻|欅|日向|高嶺の)坂?46?|"
        r"KAWAII LAB|[≠≒=]LOVE|ハロプロ|ハロー[!！]?プロジェクト", re.I)),
    ("k-pop", re.compile(r"[가-힯]|K-?POP|케이팝", re.I)),
    ("jazz-soul", re.compile(r"ジャズ|JAZZ|ソウル|\bSOUL\b|BIG ?BAND|ビッグバンド", re.I)),
    ("classical", re.compile(
        r"クラシック|CLASSICAL|オーケストラ|ORCHESTRA|交響|管弦楽|"
        r"フィルハーモ|PHILHARMONIC|弦楽四重奏|リサイタル|RECITAL", re.I)),
    ("hiphop-rnb", re.compile(
        r"HIP ?-?HOP|ヒップホップ|R&B|\bRAP\b|ラッパー|ラップ|MC ?バトル|FREESTYLE", re.I)),
    ("electronic", re.compile(
        r"TECHNO|テクノ|ELECTRO|EDM|\bRAVE\b|レイヴ|DRUM ?['&N]? ?BASS|"
        r"DJセット|DJ ?SET", re.I)),
    ("anime-seiyu", re.compile(
        r"声優|アニメ|アニソン|ラブライブ|バンドリ|BanG ?Dream|プロセカ|"
        r"ウマ娘|ホロライブ|にじさんじ|VTUBER|アイドルマスター|アイマス", re.I)),
    ("international", re.compile(
        r"来日|JAPAN ?TOUR|TOUR.{0,16}JAPAN|IN JAPAN|WORLD TOUR|"
        r"ワールドツアー|ジャパンツアー|\((?:US|UK|CA|AU|AUS|DE|FR|BR|SE|NO|NZ|IT|ES)\)", re.I)),
]

# All-night DJ events are electronic even without an explicit genre word.
_ALLNIGHT_RE = re.compile(r"オールナイト|ALL ?NIGHT", re.I)
_DJ_RE = re.compile(r"\bDJ\b|ＤＪ", re.I)

# --- art facets (category "art") -----------------------------------------
# Deterministic only: title-keyword rules FIRST (a design show at a
# nihonga museum must win as design), then the venue's collection prior.
# No LLM pass — museum programs are far more predictable than gig titles.

_ART_RULES: list[tuple[str, re.Pattern]] = [
    ("photography", re.compile(r"写真|フォト|PHOTOGRAPH|PHOTO", re.I)),
    ("manga-anime", re.compile(
        r"マンガ|漫画|アニメ|コミック|ジブリ|Fate/Grand ?Order|ハイキュー", re.I)),
    ("design", re.compile(r"デザイン|DESIGN|建築|ARCHITECT|ポスター|グラフィック", re.I)),
    ("craft", re.compile(
        r"工芸|陶磁|陶芸|やきもの|茶碗|七宝|民藝|民芸|うつわ|ガラス|漆|染織", re.I)),
    ("nihonga-classical", re.compile(
        r"日本画|古美術|浮世絵|琳派|仏教|国宝|茶の湯|刀剣|皇室|書跡|水墨|"
        r"江戸絵画|日本美術|真言|縄文|広重|源氏物語", re.I)),
    ("western-art", re.compile(
        r"西洋|印象派|ルーヴル|オルセー|ルネサンス|バロック|ゴッホ|モネ|"
        r"ピカソ|レンブラント|ターナー|大英博物館", re.I)),
    ("contemporary", re.compile(r"現代美術|現代アート|コンテンポラリー", re.I)),
]

#: what an untagged exhibition at this venue most likely is (collection
#: character). Mixed-program halls (tobikan, NACT) stay out on purpose.
_ART_VENUE_PRIOR = {
    "top_museum": "photography",
    "design_sight_2121": "design",
    "ggg": "design",
    "mot": "contemporary",
    "mori_art_museum": "contemporary",
    "opera_city_gallery": "contemporary",
    "what_museum": "contemporary",
    "nmwa": "western-art",
    "sompo": "western-art",
    "artizon": "western-art",
    "panasonic_shiodome": "western-art",
    "nezu": "nihonga-classical",
    "yamatane": "nihonga-classical",
    "mitsui": "nihonga-classical",
    "tnm": "nihonga-classical",
    "shozokan": "nihonga-classical",
    "mori_arts_center_gallery": "manga-anime",
}


def art_genres(d: dict) -> list[str]:
    """Facet(s) for one exported art event — rules first, venue prior after.
    Returns [] when neither speaks; every result is in models.ART_GENRES."""
    hay = f"{d.get('title_ja') or ''} {d.get('title_en') or ''}"
    for g, rx in _ART_RULES:
        if rx.search(hay):
            return [g]
    prior = _ART_VENUE_PRIOR.get(d.get("venue_key") or d.get("source"))
    assert prior is None or prior in ART_GENRES
    return [prior] if prior else []

#: venue prior: what an untagged music event at this source most likely is.
#: Only sources with a genuine lean get an entry — mixed venues stay out and
#: rely on rules/LLM. Priors are NOT confident (they mark LLM candidates).
_VENUE_PRIOR = {
    # band-focused live houses
    **{s: "j-rock" for s in (
        "liquidroom", "oeast", "owest", "ocrest", "onest",
        "zepp_divercity", "zepp_haneda", "zepp_shinjuku", "zepp_yokohama",
        "quattro_shibuya", "www", "www_x", "duo", "loft_shinjuku", "shelter",
        "loft_heaven", "toyosu_pit", "fever_shindaita", "que_shimokitazawa",
    )},
    # jazz clubs (Blue Note Japan group)
    "bluenote_tokyo": "jazz-soul",
    "cotton_club": "jazz-soul",
    # classical halls
    "opera_city": "classical",
    "orchard_hall": "classical",
    # idol-leaning small venues
    "shibuya_dive": "idol",
}


def _event_text(d: dict) -> str:
    parts = [d.get("title_ja"), d.get("title_en"), d.get("subtitle"),
             " ".join(d.get("lineup") or []), " ".join(d.get("tags") or [])]
    return " ".join(p for p in parts if p)


def rule_genres(d: dict) -> tuple[list[str], bool]:
    """Return (genres, confident). Not confident = venue-default only,
    i.e. a good candidate for LLM refinement."""
    text = _event_text(d)
    hits = [g for g, rx in _RULES if rx.search(text)]
    if _ALLNIGHT_RE.search(text) and _DJ_RE.search(text) \
            and "electronic" not in hits:
        hits.append("electronic")
    # "international" alone is an overlay, not a genre identity — a JAPAN
    # TOUR by an idol group is still idol-first. Keep at most 2 tags.
    hits = hits[:2]
    if hits:
        return hits, True
    prior = _VENUE_PRIOR.get(d.get("source"))
    if prior:
        return [prior], False
    return [], False


# --- LLM refinement --------------------------------------------------------

_LLM_MODEL = "claude-haiku-4-5"
#: 20×~50-token verdicts fit comfortably in max_tokens — 30 sometimes
#: truncated the JSON reply, wasting the whole batch (R11 prep)
_LLM_BATCH = 20
_LLM_MAX_PER_RUN = 150

_PROMPT = """You are tagging Tokyo concert listings with music genres.
Vocabulary (use ONLY these ids): {vocab}

"international" means an overseas (non-Japanese) artist touring Japan.
Japanese artists are never "international". K-pop acts get "k-pop".
Idol groups (Japanese underground/major idol) get "idol". Rock/pop/punk
/metal bands get "j-rock". Use at most 2 genres per event; prefer the
artist's identity over the venue. If you don't recognize the artist and
the text has no signal, answer [] for that id.

Events (JSON): {events}

Answer with ONLY a JSON object mapping each event id to a list of genre
ids, e.g. {{"abc123": ["idol"], "def456": []}}."""


def _llm_call(api_key: str, batch: list[dict]) -> dict[str, list[str]]:
    import requests
    payload = {
        "model": _LLM_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": _PROMPT.format(
            vocab=", ".join(GENRES),
            events=json.dumps(
                [{"id": d["id"],
                  "title": (d.get("title_ja") or d.get("title_en") or "")[:120],
                  "subtitle": (d.get("subtitle") or "")[:80],
                  "lineup": (d.get("lineup") or [])[:8],
                  "venue": d.get("venue_name")}
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
        if isinstance(v, list):
            out[k] = [g for g in v if g in GENRES][:2]
    return out


# --- orchestration ----------------------------------------------------------

def ensure_cache_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS genre_cache ("
                 "event_id TEXT PRIMARY KEY, genres TEXT NOT NULL, "
                 "tagger TEXT NOT NULL)")
    conn.commit()


def apply_genres(conn, events: list[dict]) -> None:
    """Fill d['genres'] in place for exported events (see precedence in
    the module docstring). Never raises — tagging must not break export."""
    ensure_cache_table(conn)
    cache = {row[0]: json.loads(row[1]) for row in
             conn.execute("SELECT event_id, genres FROM genre_cache")}

    uncertain: list[dict] = []
    for d in events:
        if d.get("category") == "art":
            if not d.get("genres"):
                d["genres"] = art_genres(d)
            continue
        if d.get("category", "music") not in ("music", "music_festival"):
            continue                   # music facets don't apply elsewhere
        if d.get("genres"):            # scraper knew best (e.g. Billboard)
            continue
        if d["id"] in cache:
            d["genres"] = cache[d["id"]]
            continue
        genres, confident = rule_genres(d)
        d["genres"] = genres
        if not confident:
            uncertain.append(d)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not uncertain:
        return
    todo = uncertain[:_LLM_MAX_PER_RUN]
    for i in range(0, len(todo), _LLM_BATCH):
        batch = todo[i:i + _LLM_BATCH]
        try:
            verdicts = _llm_call(api_key, batch)
        except Exception as e:          # LLM problems never break export
            print(f"genre LLM batch failed ({e}); keeping rule tags")
            break
        for d in batch:
            if d["id"] in verdicts:
                d["genres"] = verdicts[d["id"]] or d["genres"]
                conn.execute(
                    "INSERT OR REPLACE INTO genre_cache VALUES (?,?,?)",
                    (d["id"], json.dumps(d["genres"]), _LLM_MODEL))
    conn.commit()
