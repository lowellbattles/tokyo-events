import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.toyota_arena import ToyotaArenaScraper

FIX = Path(__file__).parent / "fixtures"
BASE = "https://www.toyota-arena-tokyo.jp"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = ToyotaArenaScraper().parse(_load("toyota_arena_tokyo_live.html"))
    return {e.source_url: e for e in evs}


def _august():
    evs = ToyotaArenaScraper().parse(_load("toyota_arena_tokyo_live_aug.html"))
    return {e.source_url: e for e in evs}


# ------------------------------------------------------------------- counts
def test_parses_exact_counts():
    # July listing renders one card per date -> 14 cards (KARA + facility tour
    # + MONSTER STRIKE x2 + 黒夢 x3 + syudou + ILLIT x3 + 桑田 x2).
    assert len(_july()) == 14
    # August listing -> 12 cards.
    assert len(_august()) == 12


# ------------------------------------------------------------ field spot-checks
def test_field_spotchecks():
    jul = _july()

    # syudou — single day 7.20, 開場：17:00 開演：18:00, artist label -> lineup.
    s = jul[f"{BASE}/events/2izio11bp-y#2026-07-20"]
    assert s.title_ja == "syudou Live 2026 「プライド」"
    assert s.start_date == "2026-07-20"
    assert (s.open_time, s.start_time) == ("17:00", "18:00")
    assert s.lineup == ["syudou"]
    assert s.category == Category.MUSIC

    # 桑田佳祐 — 7.29, "開場 17:30 開演 18:30" (marker-first, space-separated).
    k = jul[f"{BASE}/events/7v44cw_z3#2026-07-29"]
    assert k.title_ja == "桑田佳祐 LIVE TOUR 2026"
    assert (k.open_time, k.start_time) == ("17:30", "18:30")
    assert k.lineup == ["桑田佳祐"]

    # ILLIT — 7.23, "開場 17:00／開演 18:30"; the NBSP in the title collapses to
    # a normal space (the start time must not also be grabbed as the open time).
    i = jul[f"{BASE}/events/18k0cyh2qh9y#2026-07-23"]
    assert i.title_ja == "ILLIT LIVE 'PRESS START♥︎' in JAPAN"
    assert (i.open_time, i.start_time) == ("17:00", "18:30")
    assert i.lineup == ["ILLIT"]

    # KARA fanmeeting — multi-part show "1部 12:00 開場 / 13:00 開演 ...";
    # we keep the first part's times.
    kara = jul[f"https://kara2026.jp/#2026-07-04"]
    assert kara.title_ja == "【公演延期】2026 KARA JAPAN FANMEETING : Hello, KAMILIA!"
    assert (kara.open_time, kara.start_time) == ("12:00", "13:00")
    assert kara.lineup == ["KARA"]


def test_time_first_and_multi_artist():
    aug = _august()
    # ACTORS☆LEAGUE — "16:00開場 17:00開始" (time BEFORE the marker).
    a = aug[f"{BASE}/events/ouvnh74h-ta#2026-08-04"]
    assert a.title_ja == "ACTORS☆LEAGUE in Basketball 2026"
    assert (a.open_time, a.start_time) == ("16:00", "17:00")

    # ハロ！コン — slash-separated multi-act lineup from the アーティスト label.
    h = aug[f"{BASE}/events/r4vpx25y2ro#2026-08-08"]
    assert h.title_ja == "ハロ！コン 2026"
    assert h.lineup[0] == "モーニング娘。'26"
    assert "Juice=Juice" in h.lineup and "BEYOOOOONDS" in h.lineup


# --------------------------------------------------------------- detail-URL join
def test_absolute_source_urls_and_venue_meta():
    jul = _july()
    # Internal /events/ link is joined to an absolute https URL + #date.
    assert (f"{BASE}/events/x7rz23z8ko#2026-07-11") in jul
    # An externally-linked event keeps its promoter URL (still + #date).
    assert "https://kara2026.jp/#2026-07-05" in jul
    # A /pages/ facility page also joins absolutely.
    assert f"{BASE}/pages/ha6qu5iuqq/#2026-07-05" in jul
    for e in jul.values():
        assert e.source_url.startswith("https://")
        assert e.venue_name == "TOYOTA ARENA TOKYO（トヨタアリーナ東京）"
        assert e.venue_area == "Odaiba"
        assert e.genres == []


def test_multiday_kept_as_per_date_events():
    jul = _july()
    # KARA runs 7.4 and 7.5 as two cards -> two distinct, dated source_urls.
    assert "https://kara2026.jp/#2026-07-04" in jul
    assert "https://kara2026.jp/#2026-07-05" in jul
    # MONSTER STRIKE 7.11 / 7.12 -> two events, distinct slugs.
    assert f"{BASE}/events/x7rz23z8ko#2026-07-11" in jul
    assert f"{BASE}/events/d3436cjrm#2026-07-12" in jul


# ---------------------------------------------------------------- category policy
def test_category_policy_mixed_calendar():
    jul = _july()
    aug = _august()
    # Concerts / idol / K-pop fanmeetings / artist-led events -> MUSIC.
    assert jul[f"{BASE}/events/7v44cw_z3#2026-07-29"].category == Category.MUSIC
    assert jul["https://kara2026.jp/#2026-07-04"].category == Category.MUSIC
    # ACTORS☆LEAGUE "in Basketball" is an artist-led charity event -> stays MUSIC
    # (the Latin word "Basketball" is not in tu.is_nonmusic; not over-filtered).
    assert aug[f"{BASE}/events/ouvnh74h-ta#2026-08-04"].category == Category.MUSIC
    # Combat sports -> OTHER (RIZIN is in tu.is_nonmusic).
    assert aug[f"{BASE}/events/chxj_-f5-6l#2026-08-11"].category == Category.OTHER


def test_alvark_and_title_fallback_synthetic():
    # A synthetic flight card with NO image alt (exercises the bold-<p> title
    # fallback) whose title names the resident ALVARK team -> Category.OTHER via
    # the venue-specific label. Uses no OPEN/START markers -> times stay None.
    synth = (
        r'\"li\",\"synth01\",{\"className\":\"c\",\"children\":['
        r'\"$\",\"a\",null,{\"href\":\"/events/synth01\",\"children\":['
        r'\"$\",\"span\",null,{\"className\":\"bg-tat-red\",'
        r'\"children\":\"2026.9.5(土)\"}],'
        r'[\"$\",\"p\",null,{\"className\":\"font-bold md:text-xl\",'
        r'\"children\":[\"アルバルク東京 vs 千葉ジェッツ\",\"$undefined\"]}]'
        r']}]'
    )
    out = ToyotaArenaScraper().parse(synth)
    assert len(out) == 1
    ev = out[0]
    assert ev.title_ja == "アルバルク東京 vs 千葉ジェッツ"
    assert ev.start_date == "2026-09-05"
    assert ev.source_url == f"{BASE}/events/synth01#2026-09-05"
    assert ev.category == Category.OTHER
    assert (ev.open_time, ev.start_time) == (None, None)


# ---------------------------------------------------------- loud structural fail
def test_empty_html_returns_nothing_loudly():
    assert ToyotaArenaScraper().parse("<html></html>") == []
    assert ToyotaArenaScraper().parse("") == []
