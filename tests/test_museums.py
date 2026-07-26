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


# ===========================================================================
# Ring 2 (fixtures captured 2026-07-26): nezu_live.html (year schedule),
# yamatane_live.html (+ yamatane_gyokudo_live.html detail), sompo_live.html,
# design_sight_2121_live.html. Skipped ring-2 candidates: Tokyo Station
# Gallery (WAF 403), Watari-um (broken TLS), Ghibli (archive page, no ends).
# ===========================================================================
from tokyo_events.scrapers.museums import (      # noqa: E402
    DesignSightScraper, NezuScraper, SompoScraper, YamataneScraper)


def test_nezu_schedule_filters_finished_runs():
    evs = NezuScraper().parse(_load("nezu_live.html"), today=TODAY)
    # the museum is BETWEEN exhibitions on capture day: two finished runs
    # on the schedule page are dropped, one 予告 remains
    assert len(evs) == 1
    e = evs[0]
    assert e.title_ja == "企画展 やきもの名品紀行 ―中国・日本・朝鮮半島―"
    assert (e.start_date, e.end_date) == ("2026-08-15", "2026-10-12")
    assert e.source_url == \
        "https://www.nezu-muse.or.jp/jp/exhibitions/view-120.html"
    assert e.venue_name == "根津美術館"


def test_yamatane_open_cards_need_detail_pages():
    html = _load("yamatane_live.html")
    s = YamataneScraper()
    targets = s.detail_targets(html)
    # current (gyokudo) + next (togyu) cards carry no inline date
    assert targets == [
        "https://www.yamatane-museum.jp/exhibitions/2026/gyokudo.html",
        "https://www.yamatane-museum.jp/exhibitions/2026/togyu.html"]
    pages = {targets[0]: _load("yamatane_gyokudo_live.html")}
    evs = s.parse(html, detail_pages=pages, today=TODAY)
    # gyokudo resolves via its detail 会期; togyu (no page given) is skipped;
    # every archive card is in the past
    assert len(evs) == 1
    e = evs[0]
    assert e.title_ja == "川合玉堂 ―なつかしい日本の情景―"
    assert (e.start_date, e.end_date) == ("2026-05-16", "2026-07-26")


def test_yamatane_without_detail_pages_yields_nothing_current():
    evs = YamataneScraper().parse(_load("yamatane_live.html"),
                                  detail_pages={}, today=TODAY)
    assert evs == []                       # archive-only, all past


def test_sompo_current_and_next():
    evs = SompoScraper().parse(_load("sompo_live.html"))
    assert len(evs) == 2
    by_url = {e.source_url: e for e in evs}
    kayo = by_url["https://www.sompo-museum.org/exhibitions/2025/yamaguchikayo/"]
    assert kayo.title_ja == "開館50周年記念 山口華楊展"   # subtitle prefix kept
    assert (kayo.start_date, kayo.end_date) == ("2026-07-11", "2026-08-30")
    marquet = by_url["https://www.sompo-museum.org/exhibitions/2025/albertmarquet/"]
    assert (marquet.start_date, marquet.end_date) == \
        ("2026-09-22", "2026-12-13")


def test_design_sight_programs():
    evs = DesignSightScraper().parse(_load("design_sight_2121_live.html"))
    assert len(evs) == 2
    by_url = {e.source_url: e for e in evs}
    soup_ = by_url["https://www.2121designsight.jp/program/soup/"]
    assert soup_.title_ja == "企画展「スープはいのち」"
    assert (soup_.start_date, soup_.end_date) == ("2026-03-27", "2026-08-09")
    hojoki = by_url["https://www.2121designsight.jp/program/hojoki/"]
    # kanji range with padded day + cross-year explicit end
    assert (hojoki.start_date, hojoki.end_date) == ("2026-08-28", "2027-01-11")


def test_ring2_registry_flags_and_loud_failure():
    for cls in (NezuScraper, YamataneScraper, SompoScraper,
                DesignSightScraper):
        s = cls()
        assert resolve_venue(s.VENUE["venue_name"]) == s.source_id
        assert vclass_of(s.source_id) == "museum"
        assert s.supports_detail is False
        assert s.rate_limit_s >= 2.0
        assert cls().parse("<html></html>") == []


# ===========================================================================
# Ring 3 (fixtures captured 2026-07-26): mitsui_index/next_live.html,
# panasonic_shiodome_live.html (FY page behind the /exhibition/ meta-refresh
# hub), top_museum_live.html (top page slider cells), shozokan_live.json
# (WP REST CPT feed). Skipped ring-3 candidates: Idemitsu (closed for the
# Teigeki rebuild), Bunkamura The Museum (休館中, off-site shows only).
# ===========================================================================
from tokyo_events.scrapers.museums import (      # noqa: E402
    MitsuiScraper, PanasonicShiodomeScraper, ShozokanScraper,
    TopMuseumScraper, parse_slash_range)


def test_slash_range_variants():
    # Mitsui p.period: end year omitted within a year
    assert parse_slash_range("2026/7/4 (土) 〜8/30 (日)") == \
        ("2026-07-04", "2026-08-30")
    # explicit cross-year + junk rejection
    assert parse_slash_range("2026/12/20〜2027/1/11") == \
        ("2026-12-20", "2027-01-11")
    assert parse_slash_range("10:00〜17:00") == (None, None)
    assert parse_slash_range("7/4〜8/30") == (None, None)   # no start year


def test_mitsui_one_event_per_page():
    cur = MitsuiScraper().parse(_load("mitsui_index_live.html"))
    assert len(cur) == 1
    e = cur[0]
    assert e.title_ja == "特別展 京都・真如堂の名宝"
    # the dl 会期 row (full kanji range) wins over the slash p.period
    assert (e.start_date, e.end_date) == ("2026-07-04", "2026-08-30")
    assert e.source_url == "https://www.mitsui-museum.jp/exhibition/index.html"

    nxt = MitsuiScraper().parse(
        _load("mitsui_next_live.html"),
        page_url="https://www.mitsui-museum.jp/exhibition/next.html")
    assert len(nxt) == 1
    assert nxt[0].title_ja == "館蔵の茶碗100撰 ―国宝から手造茶碗まで―"
    assert (nxt[0].start_date, nxt[0].end_date) == ("2026-09-12", "2026-11-23")


def test_panasonic_fy_page_filters_finished_runs():
    evs = PanasonicShiodomeScraper().parse(
        _load("panasonic_shiodome_live.html"), today=TODAY)
    # FY page lists 5 shows; 2 already finished (終了 label is a template
    # artifact on ALL rows — the date filter is what decides)
    assert len(evs) == 3
    hasegawa = next(e for e in evs if "長谷川潔" in e.title_ja)
    assert (hasegawa.start_date, hasegawa.end_date) == \
        ("2026-07-11", "2026-09-23")
    assert "/ew/museum/exhibition/26/260711/" in hasegawa.source_url
    # next-FY item on the same page (directory /27/) is kept
    sweden = next(e for e in evs if "グスタフスベリ" in e.title_ja)
    assert (sweden.start_date, sweden.end_date) == ("2027-01-16", "2027-03-22")


def test_top_museum_slider_cells():
    evs = TopMuseumScraper().parse(_load("top_museum_live.html"))
    assert len(evs) == 6
    by_url = {e.source_url: e for e in evs}
    table = by_url["https://topmuseum.jp/exhibition/5419/"]
    assert table.title_ja == "TOPコレクション 明日の食卓"
    # machine-readable js-holiday-date data-date attrs win over text
    assert (table.start_date, table.end_date) == ("2026-07-02", "2026-09-21")
    # subtitle em is appended when present
    idemitsu_mako = by_url["https://topmuseum.jp/exhibition/5417/"]
    assert idemitsu_mako.title_ja == \
        "出光真子 おんなのさくひん ――ある映像作家の自伝"
    # film programs (/movie/) never become exhibitions
    assert not any("/movie/" in u for u in by_url)


def test_shozokan_closed_state_and_archive_filter():
    # museum closed ahead of the fall-2026 grand opening: the full-archive
    # feed (2004+) yields nothing current, and undated entries (grand
    # opening special) are never guessed into events
    evs = ShozokanScraper().parse(_load("shozokan_live.json"), today=TODAY)
    assert evs == []
    assert ShozokanScraper.allow_empty is True   # quiet feed != breakage
    # with a pinned earlier "today" the archive DOES yield real runs at
    # the museum, and other-venue stagings stay excluded
    evs = ShozokanScraper().parse(_load("shozokan_live.json"),
                                  today="2024-01-01")
    assert evs
    assert all("shozokan.nich.go.jp/exhibitions/" in e.source_url
               for e in evs)
    miyabi = next(e for e in evs if "皇室のみやび" in e.title_ja)
    assert (miyabi.start_date, miyabi.end_date) == ("2023-11-03", "2024-06-23")
    # 表慶館 (staged at TNM's building) run is excluded even when current
    assert not any("hyokeikan" in e.source_url for e in
                   ShozokanScraper().parse(_load("shozokan_live.json"),
                                           today="2026-04-20"))


def test_ring3_registry_flags_and_loud_failure():
    for cls in (MitsuiScraper, PanasonicShiodomeScraper, TopMuseumScraper,
                ShozokanScraper):
        s = cls()
        assert resolve_venue(s.VENUE["venue_name"]) == s.source_id
        assert vclass_of(s.source_id) == "museum"
        assert s.supports_detail is False
        assert s.rate_limit_s >= 2.0
        junk = "not json" if cls is ShozokanScraper else "<html></html>"
        assert cls().parse(junk) == []
