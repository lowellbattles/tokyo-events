import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.venues import norm_venue, resolve_venue, vclass_of


def test_resolves_all_probe_observed_variants():
    # exact strings seen on Sogo Tokyo / Creativeman calendars (2026-07 probe)
    cases = {
        "日本武道館": "budokan",
        "Zepp Haneda(TOKYO)": "zepp_haneda",          # no space before paren
        "Zepp DiverCity(TOKYO)": "zepp_divercity",
        "Zepp Shinjuku (TOKYO)": "zepp_shinjuku",
        "KT Zepp Yokohama": "zepp_yokohama",
        "SGC HALL ARIAKE": "sgc_hall_ariake",
        "SGC HALL 有明": "sgc_hall_ariake",
        "SGCホール有明": "sgc_hall_ariake",
        "豊洲PIT": "toyosu_pit",
        "Toyosu PIT": "toyosu_pit",
        "恵比寿 LIQUIDROOM": "liquidroom",
        "MUFGスタジアム（国立競技場）": "kokuritsu_stadium",
        "国立競技場": "kokuritsu_stadium",
        "LINE CUBE SHIBUYA（渋谷公会堂）": "line_cube_shibuya",
        "TOYOTA ARENA TOKYO（トヨタアリーナ東京）": "toyota_arena_tokyo",
        "TOYOTA ARENA TOKYO": "toyota_arena_tokyo",
        "国立代々木競技場　第一体育館": "yoyogi_gym1",   # full-width space
        "国立代々木競技場 第一体育館": "yoyogi_gym1",
        "Spotify O-WEST (旧 TSUTAYA O-WEST)": "owest",  # rename annotation
        "Spotify O-EAST": "oeast",
        "幕張メッセ　国際展示場9ホール": "makuhari_messe",  # hall suffix
        "東京国際フォーラム ホールA": "tokyo_intl_forum",
        "横浜アリーナ": "yokohama_arena",
        "Kアリーナ横浜": "k_arena_yokohama",
        "ぴあアリーナMM": "pia_arena_mm",
        "新代田FEVER": "fever_shindaita",
        "ヒューリックホール東京": "hulic_hall",
        "横浜BUNTAI": "yokohama_buntai",
        "NHKホール": "nhk_hall",
        "WWW X": "www_x",
        "WWW": "www",
    }
    for raw, expected in cases.items():
        assert resolve_venue(raw) == expected, raw


def test_unknown_and_out_of_scope_venues_resolve_none():
    for raw in ("栃木県総合文化センター", "河口湖ステラシアター",
                "club SONIC mito", "NHK大阪ホール", ""):
        assert resolve_venue(raw) is None, raw


def test_norm_venue_is_annotation_and_width_insensitive():
    assert norm_venue("Zepp Haneda(TOKYO)") == norm_venue("Zepp Haneda (TOKYO)")
    assert norm_venue("ＷＷＷ　Ｘ") == norm_venue("WWW X")


def test_gap_venues_have_classes():
    assert vclass_of("budokan") == "arena"
    assert vclass_of("blues_alley_japan") == "jazz"
    assert vclass_of("pleasure_pleasure") == "hall"


def test_curly_apostrophe_and_promoter_recovered_venues():
    assert resolve_venue("I’M A SHOW") == "im_a_show"       # U+2019
    assert resolve_venue("東京キネマ倶楽部") == "kinema_club"
    assert resolve_venue("東京体育館 メインアリーナ") == "tokyo_taiikukan"
    assert resolve_venue("ZOZOマリンスタジアム") == "zozo_marine_stadium"
