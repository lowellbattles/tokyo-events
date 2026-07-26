"""Museum sources beyond the Mori pair (scrapers/museums.py).
Fixtures captured 2026-07-26 (UTF-8, scrub-checked):
- tnm_live.html          : www.tnm.jp TOP page (the list controller redirects
                           there; current 展示 + 予告 blocks)
- mot_live.json          : /json/exhibitions/exhibitions.json (full archive)
- nact_live.html         : /exhibition_special/ (time[datetime] pairs)
- artizon_live.html      : /exhibition/ (linkBlockHover cards)
- tobikan_live.html      : /exhibition/index.html (full archive to 2012)
- nmwa_current_live.html + nmwa_upcoming_live.html : section.exb_info pages
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.museums import (
    ArtizonScraper, MotScraper, NactScraper, NmwaScraper, TnmScraper,
    TobikanScraper, parse_jp_date_range)
from tokyo_events.venues import resolve_venue, vclass_of

FIX = Path(__file__).parent / "fixtures"
TODAY = "2026-07-26"           # fixture-capture day, pins archive filters


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------- date parsing
def test_jp_date_range_variants():
    # full-year both sides (TNM), kanji parens
    assert parse_jp_date_range("2026年7月14日（火）～2026年9月6日（日）") == \
        ("2026-07-14", "2026-09-06")
    # bracket weekdays + end year omitted within a year (Artizon)
    assert parse_jp_date_range("2026年6月23日[火] - 10月4日[日]") == \
        ("2026-06-23", "2026-10-04")
    # NACT pads single-digit days with a space
    assert parse_jp_date_range("2026年9月 9日（水） ～ 2026年12月13日（日）") == \
        ("2026-09-09", "2026-12-13")
    # end year omitted across a year boundary -> inferred +1
    assert parse_jp_date_range("2026年12月20日(日)～1月11日(月)") == \
        ("2026-12-20", "2027-01-11")


def test_jp_date_range_rejects_junk():
    assert parse_jp_date_range("") == (None, None)
    assert parse_jp_date_range("会期未定") == (None, None)
    assert parse_jp_date_range("2026年3月28日[土]― 2027年1月中旬予定") == \
        (None, None)                       # fuzzy end: no second full date
    assert parse_jp_date_range("7月14日～9月6日") == (None, None)  # no year
    # two dates years apart are a schedule note, not a run
    assert parse_jp_date_range("2020年1月1日～2026年1月1日") == (None, None)


# ------------------------------------------------------------------- TNM
def test_tnm_current_and_upcoming():
    evs = {e.title_ja: e for e in
           TnmScraper().parse(_load("tnm_live.html"))}
    assert len(evs) == 5
    kukai = evs["弘法大師生誕1250年記念 特別展「空海と真言の名宝」"]
    assert (kukai.start_date, kukai.end_date) == ("2026-07-14", "2026-09-06")
    assert kukai.category is Category.ART
    assert kukai.venue_name == "東京国立博物館"
    # 予告 card (whole card is the anchor; title in the ejected p.desc)
    hiroshige = evs["特別展 内山晋コレクション受贈記念 「歌川広重 江戸のベストアングル」"]
    assert (hiroshige.start_date, hiroshige.end_date) == \
        ("2026-09-29", "2026-12-20")
    assert "r_free_page" in hiroshige.source_url
    # kids-day pickups (labeled ピックアップ) must not become exhibitions
    assert not any("キッズデー" in t for t in evs)


# ------------------------------------------------------------------- MOT
def test_mot_json_filters_archive():
    all_evs = MotScraper().parse(_load("mot_live.json"), today=TODAY)
    assert len(all_evs) == 11              # of 257 archive rows
    # MOTコレクション recurs with distinct runs/URLs — both are kept
    assert sum(1 for e in all_evs if e.title_ja == "MOTコレクション") == 2
    evs = {e.title_ja: e for e in all_evs}
    tada = evs["多田美波―光、凛と ゆれる"]
    assert (tada.start_date, tada.end_date) == ("2026-08-29", "2026-12-06")
    assert tada.source_url == \
        "https://www.mot-art-museum.jp/exhibitions/Tada-Minami/"
    # runs ending exactly today survive the cut
    assert "エリック・カール展" in evs
    assert evs["エリック・カール展"].end_date == "2026-07-26"


def test_mot_garbage_payload_is_loud():
    assert MotScraper().parse("not json", today=TODAY) == []
    assert MotScraper().parse("{}", today=TODAY) == []


# ------------------------------------------------------------------- NACT
def test_nact_time_attrs():
    evs = {e.title_ja: e for e in NactScraper().parse(_load("nact_live.html"))}
    assert len(evs) == 3
    picasso = evs["ピカソ meets ポール・スミス 遊び心の冒険へ"]
    assert (picasso.start_date, picasso.end_date) == \
        ("2026-06-10", "2026-09-21")
    assert picasso.source_url == \
        "https://www.nact.jp/exhibition_special/2026/picasso_paulsmith/"
    manga = evs["少女漫画・インフィニティ 萩尾望都×山岸凉子×大和和紀 三人展"]
    assert (manga.start_date, manga.end_date) == ("2026-10-28", "2027-02-08")


# ---------------------------------------------------------------- Artizon
def test_artizon_concurrent_floor_shows():
    evs = ArtizonScraper().parse(_load("artizon_live.html"))
    assert len(evs) == 4
    by_url = {e.source_url: e for e in evs}
    sottsass = by_url["https://www.artizon.museum/exhibition/detail/602"]
    assert sottsass.title_ja.startswith("エットレ・ソットサス")
    assert (sottsass.start_date, sottsass.end_date) == \
        ("2026-06-23", "2026-10-04")
    # two shows share the same run (different floors) — both kept
    assert sum(1 for e in evs if e.start_date == "2026-06-23") == 2
    assert sum(1 for e in evs if e.start_date == "2026-10-24") == 2


# ---------------------------------------------------------------- Tobikan
def test_tobikan_archive_filtered_to_today():
    evs = TobikanScraper().parse(_load("tobikan_live.html"), today=TODAY)
    assert len(evs) == 7                   # of a 123-item full archive
    assert all((e.end_date or e.start_date) >= TODAY for e in evs)
    orsay = next(e for e in evs if "オルセー" in e.title_ja)
    assert (orsay.start_date, orsay.end_date) == ("2026-11-14", "2027-03-28")
    assert orsay.source_url == \
        "https://www.tobikan.jp/exhibition/2026_orsay.html"
    # <br> inside .-title folds to a spaced single line
    assert any(e.title_ja == "東京都美術館開館100周年記念 "
               "この場所の風景―上野・大牟田・ブエノスアイレス" for e in evs)


# ------------------------------------------------------------------- NMWA
def test_nmwa_current_page():
    evs = NmwaScraper().parse(_load("nmwa_current_live.html"))
    assert len(evs) == 4
    by_url = {e.source_url: e for e in evs}
    rembrandt = by_url[
        "https://www.nmwa.go.jp/jp/exhibitions/2026rembrandt.html"]
    assert rembrandt.title_ja == "版画家レンブラント 挑戦、継承、インパクト"
    assert (rembrandt.start_date, rembrandt.end_date) == \
        ("2026-07-07", "2026-09-23")
    # permanent collection + fuzzy "中旬予定" runs have no parseable range
    assert not any("常設" in e.title_ja for e in evs)
    assert not any("コレクション・イン・フォーカス" in e.title_ja for e in evs)


def test_nmwa_upcoming_page():
    evs = NmwaScraper().parse(
        _load("nmwa_upcoming_live.html"),
        page_url="https://www.nmwa.go.jp/jp/exhibitions/upcoming.html")
    assert len(evs) == 1
    turner = evs[0]
    assert turner.title_ja.startswith("テート美術館")
    assert (turner.start_date, turner.end_date) == ("2026-10-24", "2027-02-21")
    assert turner.source_url == \
        "https://www.nmwa.go.jp/jp/exhibitions/2026turner.html"


# ---------------------------------------------------------------- contract
def test_all_sources_registry_flags_and_loud_failure():
    for cls in (TnmScraper, MotScraper, NactScraper, ArtizonScraper,
                TobikanScraper, NmwaScraper):
        s = cls()
        assert resolve_venue(s.VENUE["venue_name"]) == s.source_id
        assert vclass_of(s.source_id) == "museum"
        assert s.supports_detail is False
        assert s.rate_limit_s >= 2.0
        junk = "not json" if cls is MotScraper else "<html></html>"
        assert cls().parse(junk) == []     # structural failure = loud (0)
