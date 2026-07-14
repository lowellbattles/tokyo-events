"""Canonical venue resolution for cross-source matching.

Promoter calendars (Sogo Tokyo, Creativeman, ...) name the same halls
differently than the venues' own sites: "SGC HALL ARIAKE" vs "SGCホール
有明", "豊洲PIT" vs "Toyosu PIT", "Zepp Haneda(TOKYO)" without the space,
full-width vs half-width whitespace, "(旧 TSUTAYA O-WEST)" annotations.

This module normalizes venue strings and maps them onto stable venue
keys: the scraper source_id where we scrape the venue directly, or a
slug for known gap venues we only see through promoters (日本武道館...).
Resolution runs at EXPORT time so alias updates apply without re-scraping.

Venue classes (vclass) mirror the frontend filter:
livehouse / jazz / hall / arena.
"""

from __future__ import annotations

import re
import unicodedata

_PAREN_RE = re.compile(r"[（(][^（()）]*[)）]")
_WS_RE = re.compile(r"[\s　]+")

#: venue_key -> (display name, vclass). Keys that match a scraper
#: source_id are venues we scrape directly; the rest are promoter-only
#: "gap" venues inside our Tokyo/Kanagawa/Chiba/Saitama scope.
CANONICAL: dict[str, tuple[str, str]] = {
    # --- scraped sources (display = the venue_name our scrapers store) ---
    "liquidroom": ("LIQUIDROOM", "livehouse"),
    "oeast": ("Spotify O-EAST", "livehouse"),
    "owest": ("Spotify O-WEST", "livehouse"),
    "ocrest": ("Spotify O-Crest", "livehouse"),
    "onest": ("Spotify O-nest", "livehouse"),
    "zepp_divercity": ("Zepp DiverCity (TOKYO)", "livehouse"),
    "zepp_haneda": ("Zepp Haneda (TOKYO)", "livehouse"),
    "zepp_shinjuku": ("Zepp Shinjuku (TOKYO)", "livehouse"),
    "zepp_yokohama": ("KT Zepp Yokohama", "livehouse"),
    "billboard_tokyo": ("Billboard Live TOKYO", "jazz"),
    "billboard_yokohama": ("Billboard Live YOKOHAMA", "jazz"),
    "toyosu_pit": ("Toyosu PIT", "livehouse"),
    "pia_arena_mm": ("ぴあアリーナMM", "arena"),
    "quattro_shibuya": ("Shibuya CLUB QUATTRO", "livehouse"),
    "www": ("WWW", "livehouse"),
    "www_x": ("WWW X", "livehouse"),
    "duo": ("duo MUSIC EXCHANGE", "livehouse"),
    "loft_shinjuku": ("Shinjuku LOFT", "livehouse"),
    "shelter": ("下北沢SHELTER", "livehouse"),
    "loft_heaven": ("LOFT HEAVEN", "livehouse"),
    "unit_daikanyama": ("代官山UNIT", "livehouse"),
    "club_citta": ("CLUB CITTA'", "livehouse"),
    "eggman": ("Shibuya eggman", "livehouse"),
    "shibuya_dive": ("SHIBUYA DIVE", "livehouse"),
    "reny_shinjuku": ("新宿ReNY", "livehouse"),
    "que_shimokitazawa": ("下北沢CLUB Que", "livehouse"),
    "yokohama_bay_hall": ("横浜ベイホール", "livehouse"),
    "fever_shindaita": ("新代田FEVER", "livehouse"),
    "veats_shibuya": ("Veats Shibuya", "livehouse"),
    "club_seata": ("吉祥寺CLUB SEATA", "livehouse"),
    "stellar_ball": ("ステラボール", "livehouse"),
    "bluenote_tokyo": ("Blue Note Tokyo", "jazz"),
    "cotton_club": ("COTTON CLUB", "jazz"),
    "ex_theater": ("EX THEATER ROPPONGI", "hall"),
    "line_cube_shibuya": ("LINE CUBE SHIBUYA", "hall"),
    "hulic_hall": ("ヒューリックホール東京", "hall"),
    "kanadevia_hall": ("Kanadevia Hall", "hall"),
    "sgc_hall_ariake": ("SGCホール有明", "hall"),
    "tokyo_intl_forum": ("東京国際フォーラム", "hall"),
    "nhk_hall": ("NHKホール", "hall"),
    "opera_city": ("東京オペラシティ コンサートホール", "hall"),
    "tachikawa_stage_garden": ("TACHIKAWA STAGE GARDEN", "hall"),
    "orchard_hall": ("Bunkamura オーチャードホール", "hall"),
    "yokohama_arena": ("横浜アリーナ", "arena"),
    "tokyo_dome": ("東京ドーム", "arena"),
    "tokyo_garden_theater": ("東京ガーデンシアター", "arena"),
    "ariake_arena": ("有明アリーナ", "arena"),
    "toyota_arena_tokyo": ("TOYOTA ARENA TOKYO", "arena"),
    "k_arena_yokohama": ("Kアリーナ横浜", "arena"),
    "yoyogi_gym1": ("国立代々木競技場 第一体育館", "arena"),
    "kokuritsu_stadium": ("MUFGスタジアム（国立競技場）", "arena"),
    "makuhari_messe": ("幕張メッセ", "arena"),
    "yokohama_buntai": ("横浜BUNTAI", "arena"),
    # --- gap venues (promoter-covered, no direct scraper) ----------------
    "budokan": ("日本武道館", "arena"),
    "pleasure_pleasure": ("SHIBUYA PLEASURE PLEASURE", "hall"),
    "meguro_persimmon": ("めぐろパーシモンホール", "hall"),
    "yokohama_mint_hall": ("Yokohama mint hall", "livehouse"),
    "heavens_rock_saitama": ("HEAVEN'S ROCK さいたま新都心 VJ-3", "livehouse"),
    "chiba_anga": ("千葉ANGA", "livehouse"),
    "harajuku_ruido": ("原宿RUIDO", "livehouse"),
    "otemachi_mitsui_hall": ("大手町三井ホール", "hall"),
    "im_a_show": ("I'M A SHOW", "hall"),
    "grapefruit_moon": ("三軒茶屋GRAPEFRUIT MOON", "livehouse"),
    "lala_arena_tokyo_bay": ("LaLa arena TOKYO-BAY", "arena"),
    "tokyo_fm_hall": ("TOKYO FM HALL", "hall"),
    "jcom_hall_hachioji": ("J:COMホール八王子", "hall"),
    "blues_alley_japan": ("BLUES ALLEY JAPAN", "jazz"),
    "nishikawaguchi_hearts": ("西川口Hearts", "livehouse"),
    "shimokita_shangrila": ("下北沢Shangri-La", "livehouse"),
    "carats_kawasaki": ("カルッツかわさき", "hall"),
    "omiya_sonic_city": ("大宮ソニックシティ", "hall"),
    "ichikawa_bunkakaikan": ("市川市文化会館", "hall"),
    "shibuya_lovez": ("Shibuya LOVEZ", "livehouse"),
}

#: extra spellings seen in the wild -> venue_key (normalized at build time)
_EXTRA_ALIASES: dict[str, str] = {
    # promoter-side variants (Sogo Tokyo / Creativeman probes, 2026-07-14)
    "SGC HALL ARIAKE": "sgc_hall_ariake",
    "SGC HALL 有明": "sgc_hall_ariake",
    "豊洲PIT": "toyosu_pit",
    "TOYOSU PIT": "toyosu_pit",
    "恵比寿 LIQUIDROOM": "liquidroom",
    "恵比寿リキッドルーム": "liquidroom",
    "国立競技場": "kokuritsu_stadium",
    "MUFGスタジアム": "kokuritsu_stadium",
    "渋谷公会堂": "line_cube_shibuya",
    "TOKYO DOME CITY HALL": "kanadevia_hall",
    "東京ドームシティホール": "kanadevia_hall",
    "カナデビアホール": "kanadevia_hall",
    "日本武道館 (東京)": "budokan",
    "Nippon Budokan": "budokan",
    "武道館": "budokan",
    "Shinagawa Stellar Ball": "stellar_ball",
    "品川ステラボール": "stellar_ball",
    "東京国際フォーラム ホールA": "tokyo_intl_forum",
    "東京オペラシティ リサイタルホール": "opera_city",
    "Bunkamuraオーチャードホール": "orchard_hall",
    "オーチャードホール": "orchard_hall",
    "ブルーノート東京": "bluenote_tokyo",
    "コットンクラブ": "cotton_club",
    "クラブチッタ": "club_citta",
    "クラブチッタ川崎": "club_citta",
    "立川ステージガーデン": "tachikawa_stage_garden",
    "有明アリーナ（TOKYO ARIAKE ARENA）": "ariake_arena",
    "トヨタアリーナ東京": "toyota_arena_tokyo",
    "国立代々木競技場第一体育館": "yoyogi_gym1",
    "代々木第一体育館": "yoyogi_gym1",
}


def norm_venue(name: str) -> str:
    """Whitespace-, width- and annotation-insensitive venue key.
    Parenthesized segments are dropped entirely — they carry annotations
    ((TOKYO), （旧 TSUTAYA O-WEST）, （渋谷公会堂）), never identity."""
    s = unicodedata.normalize("NFKC", str(name or ""))
    s = _PAREN_RE.sub("", s)
    s = _WS_RE.sub("", s)
    return s.casefold()


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for key, (display, _cls) in CANONICAL.items():
        idx[norm_venue(display)] = key
    for alias, key in _EXTRA_ALIASES.items():
        assert key in CANONICAL, f"alias to unknown venue key: {key}"
        idx[norm_venue(alias)] = key
    return idx


_INDEX = _build_index()


def resolve_venue(name: str) -> str | None:
    """Map a raw venue string to a canonical venue key, or None if the
    venue is unknown (typically: outside our geography, or a hall we have
    not yet curated). Callers should treat None as 'drop and log'."""
    n = norm_venue(name)
    if not n:
        return None
    hit = _INDEX.get(n)
    if hit:
        return hit
    # hall-suffix tolerance: "幕張メッセ国際展示場9ホール" -> makuhari_messe,
    # "東京国際フォーラムホールA" -> tokyo_intl_forum
    for prefix, key in _INDEX.items():
        if len(prefix) >= 4 and n.startswith(prefix):
            return key
    return None


def vclass_of(key: str) -> str | None:
    entry = CANONICAL.get(key)
    return entry[1] if entry else None


def display_of(key: str) -> str | None:
    entry = CANONICAL.get(key)
    return entry[0] if entry else None
