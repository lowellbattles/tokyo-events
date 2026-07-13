import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.nhk_hall import NHKHallScraper

FIX = Path(__file__).parent / "fixtures"
# The listing carries an explicit "2026年X月" header image per month, so the
# year/month come from the page itself — `today` never affects dates. Pin a
# fixed today anyway so nothing in the suite is calendar-dependent.
TODAY = dt.date(2026, 7, 13)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    evs = NHKHallScraper().parse(_load("nhk_hall_live.html"), today=TODAY)
    # source_url is the one schedule page + a #YYYY-MM-DD fragment; key on it.
    return {e.source_url.split("#", 1)[1]: e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    # 6 monthly eventBox blocks (Jul-Dec 2026). 36 rows are visible; the
    # 7/23 高校放送コンテスト final is 一般非公開 (not open to the public) and
    # is skipped, leaving 35 attendable events.
    evs = NHKHallScraper().parse(_load("nhk_hall_live.html"), today=TODAY)
    assert len(evs) == 35
    # Every source_url is distinct (fragment disambiguation works).
    assert len({e.source_url for e in evs}) == 35


def test_field_spotchecks_current_month():
    ev = _events()
    # 7/1 — full current-month row: 開場 17:30 / 開演 18:30, 全席指定 8,000円.
    a = ev["2026-07-01"]
    assert a.title_ja == (
        "～結成20周年記念～ 高嶋ちさ子 12人のヴァイオリニスト "
        "コンサートツアー 2026～2027")
    assert (a.start_date, a.open_time, a.start_time) == \
        ("2026-07-01", "17:30", "18:30")
    assert a.price_min == 8000
    assert a.end_date is None
    # 7/2 青葉市子 — tiered price "SS 8,800 / S 7,800 / A 6,800 / B 4,800";
    # min across tiers = 4,800.
    b = ev["2026-07-02"]
    assert b.title_ja == "青葉市子 文月の衣紋に綴る熱帯魚"
    assert (b.open_time, b.start_time) == ("18:00", "19:00")
    assert b.price_min == 4800


def test_later_month_lean_row():
    # Aug-Dec use a leaner table: only 開演(予定), a contact name + phone, and
    # NO 開場/終演/price columns. Parser must still emit the event with just a
    # start time and leave open_time / price empty (not crash or mis-slice).
    ev = _events()
    char = ev["2026-09-04"]           # 9/4 Char 50th Anniversary Tour
    assert char.title_ja == "Char 50th Anniversary Tour"
    assert char.start_time == "18:30"
    assert char.open_time is None
    assert char.price_min is None
    assert char.category == Category.MUSIC


def test_multiday_runs_collapse_to_one_event():
    ev = _events()
    # 7/10-7/12 英国ロイヤル・バレエ団『ジゼル』 rowspans 3 date rows -> ONE event,
    # times taken from the first performance (17:30 / 18:30).
    giselle = ev["2026-07-10"]
    assert giselle.start_date == "2026-07-10"
    assert giselle.end_date == "2026-07-12"
    assert (giselle.open_time, giselle.start_time) == ("17:30", "18:30")
    # 7/15-7/16 SHOGO HAMADA rowspans 2 rows.
    hamada = ev["2026-07-15"]
    assert hamada.start_date == "2026-07-15"
    assert hamada.end_date == "2026-07-16"
    # No stray single-day duplicate got emitted for the trailing rows.
    assert "2026-07-11" not in ev
    assert "2026-07-12" not in ev
    assert "2026-07-16" not in ev


def test_absolute_urls_and_venue_meta():
    ev = _events()
    a = ev["2026-07-01"]
    assert a.source_url == \
        "https://www.nhk-fdn.or.jp/nhk_hall/event.html#2026-07-01"
    for e in ev.values():
        assert e.source_url.startswith(
            "https://www.nhk-fdn.or.jp/nhk_hall/event.html#")
        assert e.venue_name == "NHKホール"
        assert e.venue_area == "Shibuya"
        assert e.genres == []            # tagging happens at export
        assert e.ticket_links == []      # venue has no purchase links


def test_year_comes_from_page_header_not_today():
    # A wildly different `today` must NOT shift the dates — the year is read
    # from the "2026年7月" header image, so the page is self-dating.
    evs = NHKHallScraper().parse(
        _load("nhk_hall_live.html"), today=dt.date(2030, 1, 1))
    by = {e.source_url.split("#", 1)[1]: e for e in evs}
    assert by["2026-07-01"].start_date == "2026-07-01"


# --------------------------------------------------------------- category
def test_nonpublic_row_is_skipped():
    ev = _events()
    # The 7/23 高校放送コンテスト 決勝 is marked 一般非公開 -> not emitted.
    assert not any("放送コンテスト" in (e.title_ja or "") for e in ev.values())
    # It was the only 7/23 booking, so that date is absent entirely.
    assert "2026-07-23" not in ev


def test_all_concerts_are_music_but_nonmusic_row_is_other():
    # Every attendable booking in this window is a concert/live -> MUSIC.
    for e in _events().values():
        assert e.category == Category.MUSIC
    # A synthetic non-concert row (sumo) matched by tu.is_nonmusic -> OTHER.
    synth = (
        '<div class="eventBox">'
        '<div class="header_month"><h3>'
        '<img src="img/month/202607.gif" alt="2026年7月"></h3></div>'
        '<div class="event_top"><table><thead><tr>'
        '<th class="celDays">日・曜日</th><th class="celName">催物名</th>'
        '<th class="celOpen">開場</th><th class="celStart">開演</th>'
        '<th class="celEnd">終演</th><th class="celAbout">主催・問合せ先</th>'
        '</tr></thead></table></div>'
        '<table class="part"><tbody><tr>'
        '<td class="celDays"><p class="dayText">6日（月）</p></td>'
        '<td class="celName"><p><strong>大相撲 夏巡業 渋谷場所</strong></p></td>'
        '<td class="celOpen">10:00</td><td class="celStart">11:00</td>'
        '<td class="celEnd">15:00</td>'
        '<td class="celAbout"><dl class="wrap"><dt>備考：</dt>'
        '<dd>全席指定　5,000円</dd></dl></td>'
        '</tr></tbody></table></div>')
    out = NHKHallScraper().parse(synth, today=TODAY)
    assert len(out) == 1
    assert out[0].category == Category.OTHER
    assert out[0].start_date == "2026-07-06"


def test_empty_and_structureless_html_returns_nothing():
    # Loud structural failure: no eventBox -> no events (never garbage).
    assert NHKHallScraper().parse("<html></html>", today=TODAY) == []
    assert NHKHallScraper().parse("", today=TODAY) == []
    # An eventBox whose kanji <thead> headers vanished must yield nothing
    # rather than blindly slicing columns by position.
    no_headers = (
        '<div class="eventBox">'
        '<div class="header_month"><h3>'
        '<img src="x" alt="2026年7月"></h3></div>'
        '<table class="part"><tbody><tr>'
        '<td class="celDays"><p class="dayText">1日</p></td>'
        '<td class="celName"><p><strong>Whatever</strong></p></td>'
        '<td class="celStart">18:00</td></tr></tbody></table></div>')
    assert NHKHallScraper().parse(no_headers, today=TODAY) == []
