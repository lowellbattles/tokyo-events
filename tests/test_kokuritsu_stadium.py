import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.kokuritsu_stadium import KokuritsuStadiumScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    return {e.source_url: e
            for e in KokuritsuStadiumScraper().parse(
                _load("kokuritsu_stadium_live.html"))}


def _august():
    return {e.source_url: e
            for e in KokuritsuStadiumScraper().parse(
                _load("kokuritsu_stadium_live_aug.html"))}


# ------------------------------------------------------------------- listing
def test_july_event_count():
    # Current-month (2026-07) fixture holds exactly 2 rows: one concert, one
    # rugby fixture. Both are kept; the mismatch shows up loudly in the count
    # if the Next.js markup churns.
    assert len(_july()) == 2


def test_concert_row_fields_and_multiday():
    # Mrs. GREEN APPLE stadium show — the marquee music row.
    ev = _july()["https://jns-e.com/event/20260309-453/"]
    assert ev.title_ja == "【FC会員限定公演】ゼンジン未到とイ/ミュータブル～間奏編～"
    assert ev.category == Category.MUSIC
    assert ev.start_date == "2026-07-04"      # 日程 first day-wrapper
    assert ev.end_date == "2026-07-05"        # second day-wrapper -> multi-day
    assert ev.open_time == "16:30"            # 開場16:30
    assert ev.start_time == "18:30"           # 開演18:30
    assert ev.lineup == ["Mrs. GREEN APPLE"]  # アーティスト field
    # venue metadata + naming-rights alias display name
    assert ev.venue_name == "MUFGスタジアム（国立競技場）"
    assert ev.venue_area == "Sendagaya"


def test_category_policy_sport_row_is_other():
    # A rugby fixture (icon tag スポーツ) must be Category.OTHER, never MUSIC —
    # this is the single most important rule for a stadium calendar.
    ev = _july()["https://jns-e.com/event/20260718_event/"]
    assert ev.title_ja == "ネーションズチャンピオンシップ2026 日本代表vsフランス代表"
    assert ev.category == Category.OTHER
    assert ev.start_date == "2026-07-18"
    # "17:40 キックオフ" carries no 開場/開演 -> no concert times invented
    assert ev.open_time is None
    assert ev.start_time is None


def test_absolute_detail_urls():
    evs = _july()
    assert all(u.startswith("https://jns-e.com/event/") for u in evs)


# ---------------------------------------------------------- second month page
def test_august_mixed_calendar_split():
    evs = _august()
    assert len(evs) == 8                                   # 7 sport + 1 music
    music = [e for e in evs.values() if e.category == Category.MUSIC]
    other = [e for e in evs.values() if e.category == Category.OTHER]
    assert len(music) == 1
    assert len(other) == 7


def test_august_concert_row():
    ev = _august()["https://jns-e.com/event/20260829-0830_event/"]
    assert ev.title_ja == "Stray Kids World Tour ＜RUN IT JAPAN＞"
    assert ev.category == Category.MUSIC
    assert ev.start_date == "2026-08-29"
    assert ev.end_date == "2026-08-30"
    # multi-show time string "…8/29（土）開場15:30 開演17:30 8/30（日）…" -> first pair
    assert ev.open_time == "15:30"
    assert ev.start_time == "17:30"
    assert ev.lineup == ["Stray Kids"]


def test_empty_html_returns_no_events_loud_failure():
    assert KokuritsuStadiumScraper().parse("<html></html>") == []
