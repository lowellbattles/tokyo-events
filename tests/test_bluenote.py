"""Tests for the Blue Note Japan group scrapers (bluenote.py).

Fixtures saved live 2026-07-13:
- bluenote_tokyo_live.html    : the /jp/ homepage schedule (<ul id=upcomingData>)
- bluenote_tokyo_detail.html  : /jp/artists/fox-capture-plan/ detail page
- cotton_club_live.html       : reserve .../schedule/move/202607 (July) month page
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.bluenote import (
    BlueNoteTokyoScraper, CottonClubScraper,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _slug(url):
    return url.rstrip("/").split("/")[-1]


# ===========================================================================
# Blue Note Tokyo — listing
# ===========================================================================
def _bn_events():
    return {_slug(e.source_url): e
            for e in BlueNoteTokyoScraper().parse(_load("bluenote_tokyo_live.html"))}


def test_bluenote_parses_exact_event_count():
    # 47 <li date-start=...> rows under <ul id="upcomingData">.
    assert len(_bn_events()) == 47


def test_bluenote_single_and_multiday_fields():
    evs = _bn_events()
    # Multi-day run: JUJU JAZZ LIVE, 11.12–11.18 (title stacked on <br>).
    juju = evs["juju"]
    assert juju.title_ja == "JUJU JAZZ LIVE 2026"
    assert juju.subtitle == "EVERYTHING IS DELICIOUS IN AUTUMN!!"
    assert juju.start_date == "2026-11-12"
    assert juju.end_date == "2026-11-18"
    assert juju.category == Category.MUSIC
    assert juju.venue_name == "Blue Note Tokyo (ブルーノート東京)"
    assert juju.venue_area == "Minami-Aoyama"

    # Multi-day run with no subtitle.
    jake = evs["jake-shimabukuro"]
    assert jake.title_ja == "JAKE SHIMABUKURO"
    assert jake.start_date == "2026-07-10"
    assert jake.end_date == "2026-07-12"


def test_bluenote_single_day_has_no_end_date():
    # ASAKO TOKI is a one-night show (date-start == date-end).
    e = _bn_events()["asako-toki"]
    assert e.start_date == "2026-07-15"
    assert e.end_date is None


def test_bluenote_html_comment_stripped_from_title():
    # <a><!--38thアニバーサリー-->TAKUYA KURODA</a> — comment must not leak.
    e = _bn_events()["takuya-kuroda"]
    assert e.title_ja == "TAKUYA KURODA"
    assert "38" not in (e.title_ja or "")


def test_bluenote_detail_urls_are_absolute_https():
    evs = _bn_events()
    assert evs["juju"].source_url == "https://www.bluenote.co.jp/jp/artists/juju/"
    assert all(e.source_url.startswith("https://www.bluenote.co.jp/jp/artists/")
               for e in evs.values())


def test_bluenote_all_rows_music():
    # Pure jazz club — the defensive is_nonmusic guard must keep every row.
    assert all(e.category == Category.MUSIC for e in _bn_events().values())


def test_bluenote_empty_html_yields_no_events():
    assert BlueNoteTokyoScraper().parse("<html></html>") == []
    assert BlueNoteTokyoScraper().parse("") == []


# ===========================================================================
# Blue Note Tokyo — detail enrichment (custom parse_detail)
# ===========================================================================
def _bn_detail(**overrides):
    ev = Event(source="bluenote_tokyo",
               source_url="https://www.bluenote.co.jp/jp/artists/fox-capture-plan/",
               title_ja="fox capture plan", start_date="2026-08-20",
               **overrides)
    return BlueNoteTokyoScraper().parse_detail(
        _load("bluenote_tokyo_detail.html"), ev)


def test_bluenote_detail_times_are_earliest_set_in_24h():
    # "[1st]Open5:00pm Start6:00pm  [2nd]Open7:45pm Start8:30pm" -> 1st set,
    # 12h am/pm converted to 24h.
    ev = _bn_detail()
    assert (ev.open_time, ev.start_time) == ("17:00", "18:00")


def test_bluenote_detail_base_music_charge_not_seat_tiers():
    # Headline ￥7,500; the higher per-seat tiers must not be chosen.
    ev = _bn_detail()
    assert ev.price_min == 7500
    assert "7,500" in ev.price_text


def test_bluenote_detail_reserve_ticket_link():
    ev = _bn_detail()
    bn = [t for t in ev.ticket_links if t["provider"] == "bluenote"]
    assert bn and bn[0]["url"] == (
        "https://reserve.bluenote.co.jp/reserve/schedule/show_event_info/3970/")


def test_bluenote_detail_lineup_from_member_table():
    ev = _bn_detail()
    # EN names, instrument parenthetical stripped.
    assert ev.lineup[0] == "Ryo Kishimoto"
    assert "Hidehiro Kawai" in ev.lineup


def test_bluenote_detail_does_not_overwrite_listing_fields():
    ev = _bn_detail(open_time="12:00", start_time="13:00", price_min=999)
    assert (ev.open_time, ev.start_time, ev.price_min) == ("12:00", "13:00", 999)


# ===========================================================================
# COTTON CLUB — listing (reserve month page)
# ===========================================================================
def _cc_key(url):
    # slug + '#' + date fragment, e.g. "joyce-moreno-260702#2026-07-02".
    tail = url.split("/jp/sp/artists/")[-1]
    slug, _, frag = tail.partition("#")
    return f"{slug.rstrip('/')}#{frag}"


def _cc_events():
    return {_cc_key(e.source_url): e
            for e in CottonClubScraper().parse(_load("cotton_club_live.html"),
                                               month=dt.date(2026, 7, 1))}


def test_cotton_parses_exact_event_count():
    # 18 <div class="detailsOpen"> event blocks on the July 2026 page
    # (incl. a two-night run under one slug, kept distinct by #date).
    assert len(_cc_events()) == 18


def test_cotton_title_price_times_and_date():
    e = _cc_events()["joyce-moreno-260702#2026-07-02"]
    assert e.title_ja == "JOYCE MORENO - Celebrating Feminina 45th Anniversary -"
    assert e.start_date == "2026-07-02"       # visible "2026 7.2 thu." span
    assert e.price_min == 9500                 # "Music charge ¥9,500"
    assert (e.open_time, e.start_time) == ("17:00", "18:00")  # 1st.show, 24h
    assert e.category == Category.MUSIC
    assert e.venue_name == "COTTON CLUB"
    assert e.venue_area == "Marunouchi"


def test_cotton_afternoon_show_and_higher_price():
    e = _cc_events()["breakerz-260704#2026-07-04"]
    assert e.start_date == "2026-07-04"
    assert e.price_min == 15000
    assert (e.open_time, e.start_time) == ("15:30", "16:30")


def test_cotton_two_night_run_shares_slug_distinct_by_date_fragment():
    # ryoko-hirosue plays 7.18 and 7.24; both link the same slug
    # (ryoko-hirosue-260718) but must survive as two events via #date.
    evs = _cc_events()
    n18 = evs["ryoko-hirosue-260718#2026-07-18"]
    n24 = evs["ryoko-hirosue-260718#2026-07-24"]
    assert n18.start_date == "2026-07-18"
    assert n24.start_date == "2026-07-24"
    base = "https://www.cottonclubjapan.co.jp/jp/sp/artists/ryoko-hirosue-260718/"
    assert n18.source_url == base + "#2026-07-18"
    assert n24.source_url == base + "#2026-07-24"


def test_cotton_visible_date_overrides_misleading_slug_date():
    # Slug says 260630 (June 30) but the row is printed under July 9 —
    # the visible date wins over the slug's stale id.
    e = _cc_events()["motoharu-sano-260630#2026-07-09"]
    assert e.start_date == "2026-07-09"


def test_cotton_detail_urls_are_absolute_https():
    evs = _cc_events()
    base = "https://www.cottonclubjapan.co.jp/jp/sp/artists/"
    assert evs["joyce-moreno-260702#2026-07-02"].source_url == (
        base + "joyce-moreno-260702/#2026-07-02")
    assert all(e.source_url.startswith(base) for e in evs.values())


def test_cotton_category_guard_keeps_concerts_music():
    # No site-provided category tags and is_nonmusic trips on none of these
    # rows, so every booking classifies MUSIC (incl. the rare talk-style
    # event, which the guard cannot catch by keyword — see caveats).
    assert all(e.category == Category.MUSIC for e in _cc_events().values())


def test_cotton_empty_html_yields_no_events():
    assert CottonClubScraper().parse("<html></html>") == []
    assert CottonClubScraper().parse("") == []
