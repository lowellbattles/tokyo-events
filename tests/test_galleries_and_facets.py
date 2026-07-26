"""Gallery ring (scrapers/galleries.py) + art-facet taxonomy (genres.py).
Fixtures captured 2026-07-26 (UTF-8, scrub-checked):
- opera_city_gallery_{current,upcoming}_live.html : /contents/exhibition/
  fragments (the public pages are JS shells rendered from these)
- what_museum_list_live.html + what_museum_detail_live.html
- ggg_live.html : dnpfcp.jp/gallery/ggg/ top page
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.genres import apply_genres, art_genres
from tokyo_events.models import ART_GENRES, Category
from tokyo_events.scrapers.galleries import (GggScraper, OcagScraper,
                                             WhatMuseumScraper)
from tokyo_events.venues import resolve_venue, vclass_of

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------- OCAG
def test_ocag_current_shares_one_run_with_stable_identities():
    evs = OcagScraper().parse(_load("opera_city_gallery_current_live.html"))
    assert len(evs) == 3
    assert all((e.start_date, e.end_date) == ("2026-07-18", "2026-09-23")
               for e in evs)
    by_url = {e.source_url: e for e in evs}
    # identity canonicalized from the image path id — the vanity /exh300/
    # link still maps to detail.php?id=325
    aoki = by_url["https://www.operacity.jp/ag/exh/detail.php?id=325"]
    assert aoki.title_ja == "ほぼ、空：青木淳 + リチャード・タトル"
    # floor labels (nameSub) never leak into titles
    assert not any("ギャラリー" in e.title_ja or "コリドール" in e.title_ja
                   for e in evs)


def test_ocag_upcoming_sections_and_link_free_items():
    evs = OcagScraper().parse(_load("opera_city_gallery_upcoming_live.html"))
    assert len(evs) == 6
    by_url = {e.source_url: e for e in evs}
    # items carry NO anchors — identity still derives from the image path
    seeds = by_url["https://www.operacity.jp/ag/exh/detail.php?id=334"]
    assert seeds.title_ja == "種と根っこ ─ 都市の耕し方"
    assert (seeds.start_date, seeds.end_date) == ("2026-10-17", "2026-12-20")
    franck = by_url["https://www.operacity.jp/ag/exh/detail.php?id=331"]
    assert (franck.start_date, franck.end_date) == ("2027-01-16", "2027-03-22")


# ------------------------------------------------------------- WHAT MUSEUM
def test_what_museum_detail_kaiki_wins_over_card_start_date():
    s = WhatMuseumScraper()
    listing = _load("what_museum_list_live.html")
    targets = s.detail_targets(listing)
    # exhibitions only — /events/ talk links never make the target list
    assert all("/exhibitions/" in t for t in targets)
    assert len(targets) == 8
    url = next(t for t in targets if t.endswith("corrugatedcoral"))
    evs = s.parse(listing, detail_pages={
        url: _load("what_museum_detail_live.html")})
    assert len(evs) == 1                   # cards without a fetched detail skip
    e = evs[0]
    assert e.title_ja == "波板と珊瑚礁 ー 建築を遠くに投げる八の実践"
    # card shows only 2026年4月21日; the th会期/td row carries the real run
    assert (e.start_date, e.end_date) == ("2026-04-21", "2026-09-13")
    assert e.category is Category.ART


# -------------------------------------------------------------------- ggg
def test_ggg_current_show():
    evs = GggScraper().parse(_load("ggg_live.html"))
    assert len(evs) == 1
    e = evs[0]
    # ttl02 only — the 第415回企画展 series label (ttl01) stays out
    assert e.title_ja.startswith("ダフィ・クーネ：ポスターを構築する")
    assert "第415回" not in e.title_ja
    assert (e.start_date, e.end_date) == ("2026-07-14", "2026-08-26")
    # the 詳細 link lives outside box-information but is still found
    assert "schedule/detail.cgi" in e.source_url


# ------------------------------------------------------------- art facets
def test_art_rules_beat_venue_priors():
    # a design show at a nihonga museum tags design, not the prior
    assert art_genres({"title_ja": "民藝のデザイン", "source": "nezu"}) == \
        ["design"]
    assert art_genres({"title_ja": "写真と絵画", "source": "mot"}) == \
        ["photography"]


def test_art_priors_cover_untitled_matches():
    assert art_genres({"title_ja": "ロン・ミュエク",
                       "source": "mori_art_museum"}) == ["contemporary"]
    assert art_genres({"title_ja": "多田美波―光、凛と ゆれる",
                       "source": "mot"}) == ["contemporary"]
    assert art_genres({"title_ja": "山口華楊展", "source": "sompo"}) == \
        ["western-art"]
    # mixed-program halls have no prior -> honest empty
    assert art_genres({"title_ja": "この場所の風景", "source": "tobikan"}) == []


def test_art_rule_samples_from_live_data():
    cases = {
        "Fate/Grand Order展 -星見の回廊-": "manga-anime",
        "古舘春一 ハイキュー!!展 挑戦者たち": "manga-anime",
        "弘法大師生誕1250年記念 特別展「空海と真言の名宝」": "nihonga-classical",
        "ルーヴル美術館展 ルネサンス": "western-art",
        "TOPコレクション 明日の食卓": None,      # prior handles it
        "企画展「スープはいのち」": None,
        "版画家レンブラント 挑戦、継承、インパクト": "western-art",
        "カイ・フランク展 時代を超えるフィンランド・デザイン": "design",
    }
    for title, want in cases.items():
        got = art_genres({"title_ja": title, "source": "zzz-no-prior"})
        assert got == ([want] if want else []), title
    assert all(g in ART_GENRES
               for t in cases for g in art_genres({"title_ja": t,
                                                   "source": "zzz"}))


def test_apply_genres_tags_art_without_touching_music(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "g.db")
    events = [
        {"id": "a1", "category": "art", "title_ja": "写真の世紀",
         "source": "mitsui", "genres": []},
        {"id": "a2", "category": "art", "title_ja": "無題の展覧会",
         "source": "top_museum", "genres": []},
        {"id": "m1", "category": "music", "title_ja": "ジャズナイト",
         "source": "x", "genres": []},
    ]
    apply_genres(conn, events)
    assert events[0]["genres"] == ["photography"]   # rule beats mitsui prior
    assert events[1]["genres"] == ["photography"]   # venue prior
    assert events[2]["genres"] == ["jazz-soul"]     # music path untouched


# ---------------------------------------------------------------- contract
def test_gallery_registry_flags_and_loud_failure():
    for cls in (OcagScraper, WhatMuseumScraper, GggScraper):
        s = cls()
        assert resolve_venue(s.VENUE["venue_name"]) == s.source_id
        assert vclass_of(s.source_id) in ("museum", "gallery")
        assert s.supports_detail is False
        assert s.rate_limit_s >= 2.0
        assert cls().parse("<html></html>") == []
    assert vclass_of("ggg") == "gallery"
