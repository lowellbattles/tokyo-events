"""Tests for the Blue Note Japan group scrapers (bluenote.py).

Fixtures saved live 2026-07-13:
- bluenote_tokyo_live.html    : the /jp/ homepage schedule (<ul id=upcomingData>)
- bluenote_tokyo_detail.html  : /jp/artists/fox-capture-plan/ detail page

COTTON CLUB fixtures saved live 2026-09-24, after the reservation site's
redesign broke the old scheduleTable/detailsOpen parser (found=0 since
2026-09-01):
- cotton_club_202609_live.html : reserve .../schedule/move/202609 (Sept) —
  the new m-schedule-list listing panel, incl. a "Tonight" date-badge row,
  two multi-night runs of the same title under different exec ids
  (5686 = Sept 4-7, 5687 = Sept 9-13), and closed/OFF days.
- cotton_club_202610_live.html : .../move/202610 (Oct) — more multi-night
  runs, plus "type-empty" (no show announced) days.
- cotton_club_exec_5716.html   : .../schedule/exec/5716 — a single-night,
  two-stage (1st/2nd) reservation page.
- cotton_club_exec_5719.html   : .../schedule/exec/5719 — a 3-night run
  (Oct 13-15) sharing one exec id, same 1st/2nd times every night.
- cotton_club_exec_5743.html   : .../schedule/exec/5743 — a 3-night run
  (Oct 16-18) where the FIRST night has only one stage and the other two
  have two, exercising per-night (not per-run) time lookup.
- cotton_club_exec_5738.html   : .../schedule/exec/5738 — a past show past
  its reservation window (fetched live 2026-09-24); the site serves a
  "ご予約は受付終了" error page instead of the time table.
- cotton_club_exec_5744.html   : .../schedule/exec/5744 — the reservation
  engine is mid-migration: most exec pages above still serve the OLD flow,
  but this one already serves the NEW one (.c-date-slot-group per night,
  data-theme="cotton-club" like the redesigned listing) — same venue, same
  day, two live template shapes.
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
# COTTON CLUB — listing (reserve month page, redesigned template)
# ===========================================================================
def _cc_key(url):
    # trailing exec id (+ optional #date fragment), e.g. "5686#2026-09-04".
    return url.split("/exec/")[-1]


def _cc_sept_events():
    return {_cc_key(e.source_url): e
            for e in CottonClubScraper().parse(
                _load("cotton_club_202609_live.html"), month=dt.date(2026, 9, 1))}


def _cc_oct_events():
    return {_cc_key(e.source_url): e
            for e in CottonClubScraper().parse(
                _load("cotton_club_202610_live.html"), month=dt.date(2026, 10, 1))}


def test_cotton_parses_exact_event_count():
    # 16 real .c-schedule-list-card rows on the Sept page; two of them are
    # multi-night runs (5686: 4 nights, 5687: 5 nights) that each expand
    # into one Event per night, for 12 single-night + 4 + 5 + 2 (5708) +
    # 2 (5738) = 25. Closed/OFF days (01,02,03,08,18) contribute nothing.
    assert len(_cc_sept_events()) == 25


def test_cotton_single_night_show_has_no_date_fragment():
    e = _cc_sept_events()["5728"]
    assert e.title_ja == "NAOKO TERAI QUARTET"
    assert e.subtitle == '"The Precious Night 2026" at COTTON CLUB'
    assert e.start_date == "2026-09-14"
    assert e.price_min == 8000
    assert "8,000" in e.price_text
    assert e.category == Category.MUSIC
    assert e.venue_name == "COTTON CLUB"
    assert e.venue_area == "Marunouchi"
    assert e.source_url == "https://reserve.cottonclubjapan.co.jp/reserve/schedule/exec/5728"


def test_cotton_multinight_run_expands_one_event_per_night():
    # "コントと音楽 vol.7" plays four consecutive nights (Sept 4-7) under
    # one card/exec id (5686) — must survive as four distinct Events.
    evs = _cc_sept_events()
    nights = ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07"]
    for d in nights:
        e = evs[f"5686#{d}"]
        assert e.title_ja == "コントと音楽 vol.7"
        assert e.subtitle == "「今宵、丸の内で」"
        assert e.start_date == d
        assert e.price_min == 11000
        assert e.source_url == (
            f"https://reserve.cottonclubjapan.co.jp/reserve/schedule/exec/5686#{d}")


def test_cotton_repeat_booking_of_same_title_gets_distinct_exec_id():
    # The SAME show ("コントと音楽 vol.7") also runs Sept 9-13 under a
    # DIFFERENT exec id (5687) — two separate bookings, not one bled run.
    evs = _cc_sept_events()
    assert evs["5686#2026-09-04"].source_url != evs["5687#2026-09-09"].source_url
    assert evs["5687#2026-09-13"].start_date == "2026-09-13"


def test_cotton_today_badge_variant_still_parses():
    # "Tonight" rows wrap their date-num in .date-badge instead of
    # .date-item — must still be picked up as a normal single-night event.
    e = _cc_sept_events()["5717"]
    assert e.title_ja == "ULYSSES OWENS JR. & GENERATION Y"
    assert e.start_date == "2026-09-24"
    assert e.price_min == 9500


def test_cotton_closed_and_empty_days_produce_no_events():
    sept_dates = {e.start_date for e in _cc_sept_events().values()}
    for closed in ("2026-09-01", "2026-09-02", "2026-09-03",
                   "2026-09-08", "2026-09-18"):
        assert closed not in sept_dates

    oct_evs = _cc_oct_events()
    oct_dates = {e.start_date for e in oct_evs.values()}
    for closed in ("2026-10-04", "2026-10-05", "2026-10-22",
                   "2026-10-24", "2026-10-31",     # PRIVATE
                   "2026-10-23", "2026-10-28"):    # type-empty, no show yet
        assert closed not in oct_dates
    assert len(oct_evs) == 24


def test_cotton_category_guard_keeps_concerts_music():
    assert all(e.category == Category.MUSIC for e in _cc_sept_events().values())


def test_cotton_empty_html_yields_no_events():
    assert CottonClubScraper().parse("<html></html>") == []
    assert CottonClubScraper().parse("") == []


# ===========================================================================
# COTTON CLUB — detail enrichment (exec/<id> reservation page)
# ===========================================================================
def _cc_detail(fixture, date, **overrides):
    ev = Event(source="cotton_club",
               source_url=f"https://reserve.cottonclubjapan.co.jp/reserve/"
                          f"schedule/exec/x#{date}",
               title_ja="t", start_date=date, **overrides)
    return CottonClubScraper().parse_detail(_load(fixture), ev)


def test_cotton_detail_single_night_two_stage_earliest_set_in_24h():
    # exec/5716: "[1st] Open 2:30pm Start 3:30pm" / "[2nd] Open 5:30pm
    # Start 6:30pm" -> earliest (1st) set, converted to 24h.
    ev = _cc_detail("cotton_club_exec_5716.html", "2026-09-26")
    assert (ev.open_time, ev.start_time) == ("14:30", "15:30")


def test_cotton_detail_multinight_run_same_times_every_night():
    # exec/5719 (Oct 13-15): each night has its own .threeColumnsTypeBg
    # block but the same 1st/2nd times — confirms per-night date lookup,
    # not "first block on the page" lookup.
    for d in ("2026-10-13", "2026-10-14", "2026-10-15"):
        ev = _cc_detail("cotton_club_exec_5719.html", d)
        assert (ev.open_time, ev.start_time) == ("17:00", "18:00")


def test_cotton_detail_multinight_run_times_vary_by_night():
    # exec/5743 (Oct 16-18): night 1 has ONE stage (Open 6/Start 7pm),
    # nights 2-3 have TWO (earliest Open 3/Start 4pm) — proves the lookup
    # keys off e_date, not stage position or "first block found".
    d1 = _cc_detail("cotton_club_exec_5743.html", "2026-10-16")
    assert (d1.open_time, d1.start_time) == ("18:00", "19:00")
    d2 = _cc_detail("cotton_club_exec_5743.html", "2026-10-17")
    assert (d2.open_time, d2.start_time) == ("15:00", "16:00")
    d3 = _cc_detail("cotton_club_exec_5743.html", "2026-10-18")
    assert (d3.open_time, d3.start_time) == ("15:00", "16:00")


def test_cotton_detail_closed_reservation_window_is_not_an_error():
    # exec/5738 is a past show whose booking window is closed — the site
    # serves a "ご予約は受付終了" page with no time table at all. This is
    # NOT a structural failure (parse() already found the event on the
    # listing); parse_detail must just leave times/sold-out untouched.
    ev = _cc_detail("cotton_club_exec_5738.html", "2026-09-22")
    assert ev.open_time is None and ev.start_time is None
    assert ev.is_sold_out is False


def test_cotton_detail_new_template_reads_time_slot_groups():
    # exec/5744 already serves the NEW reservation flow (.c-date-slot-group
    # / .c-time-slot, radio value "YYYYMMDD_stagenum") instead of the old
    # .threeColumnsTypeBg one — same "Open 3:30pm Start 4:30pm" 1st/2nd
    # shape, different markup entirely. Must parse just the same.
    ev = _cc_detail("cotton_club_exec_5744.html", "2026-09-25")
    assert (ev.open_time, ev.start_time) == ("15:30", "16:30")
    assert ev.is_sold_out is False


def test_cotton_detail_does_not_overwrite_listing_fields():
    ev = _cc_detail("cotton_club_exec_5716.html", "2026-09-26",
                    open_time="12:00", start_time="13:00")
    assert (ev.open_time, ev.start_time) == ("12:00", "13:00")


def test_cotton_detail_sold_out_when_every_stage_is_full():
    # No sold-out show was on the live calendar when this scraper was
    # rewritten (2026-09-24), so this exercises the "selloutBg" class the
    # exec page's own JS keys off (`.columnAreaBg.hasClass('selloutBg')`)
    # against a small CONSTRUCTED snippet mirroring the real markup
    # (module docstring), not a captured fixture.
    html = """
    <div class="threeColumnsTypeBg">
      <input type="hidden" name="event_date" class="e_date" value="20260926">
      <div class="columnAreaBg r_1 selloutBg">
        <div class="number">1</div>
        <div class="time">Open <span class="ot">2:30pm</span>
          <br>Start <span class="et">3:30pm</span></div>
      </div>
      <div class="columnAreaBg r_1 selloutBg">
        <div class="number">2</div>
        <div class="time">Open <span class="ot">5:30pm</span>
          <br>Start <span class="et">6:30pm</span></div>
      </div>
    </div>
    """
    ev = _cc_detail_html(html, "2026-09-26")
    assert ev.is_sold_out is True
    assert (ev.open_time, ev.start_time) == ("14:30", "15:30")


def test_cotton_detail_not_sold_out_when_one_stage_still_has_room():
    html = """
    <div class="threeColumnsTypeBg">
      <input type="hidden" name="event_date" class="e_date" value="20260926">
      <div class="columnAreaBg r_1 selloutBg">
        <div class="time">Open <span class="ot">2:30pm</span>
          <br>Start <span class="et">3:30pm</span></div>
      </div>
      <div class="columnAreaBg r_1 reservationBtn">
        <div class="time">Open <span class="ot">5:30pm</span>
          <br>Start <span class="et">6:30pm</span></div>
      </div>
    </div>
    """
    ev = _cc_detail_html(html, "2026-09-26")
    assert ev.is_sold_out is False


def test_cotton_detail_new_template_sold_out_when_every_slot_is_full():
    # No sold-out show was observed live on either template (2026-09-24);
    # this mirrors the real .c-date-slot-group/.c-time-slot markup with a
    # CONSTRUCTED snippet (module docstring), keying off the same
    # data-status="sold-out" the page's own legend defines.
    html = """
    <div class="c-date-slot-group">
      <p class="c-date-slot-group__label">2026 9.25 fri.</p>
      <div class="c-date-slot-group__slots">
        <label class="c-time-slot" data-status="sold-out">
          <input class="u-sr-only" type="radio" name="time-slot" value="20260925_1" />
          <span class="c-time-slot__label">1st</span>
          <span class="c-time-slot__times"><span class="c-time-slot__time-type">Open</span>
          <span class="c-time-slot__time-value">3:30pm</span>
          <span class="c-time-slot__time-type">Start</span>
          <span class="c-time-slot__time-value">4:30pm</span></span>
        </label>
        <label class="c-time-slot" data-status="sold-out">
          <input class="u-sr-only" type="radio" name="time-slot" value="20260925_2" />
          <span class="c-time-slot__label">2nd</span>
          <span class="c-time-slot__times"><span class="c-time-slot__time-type">Open</span>
          <span class="c-time-slot__time-value">6:30pm</span>
          <span class="c-time-slot__time-type">Start</span>
          <span class="c-time-slot__time-value">7:30pm</span></span>
        </label>
      </div>
    </div>
    """
    ev = _cc_detail_html(html, "2026-09-25")
    assert ev.is_sold_out is True
    assert (ev.open_time, ev.start_time) == ("15:30", "16:30")


def _cc_detail_html(html, date, **overrides):
    ev = Event(source="cotton_club",
               source_url=f"https://reserve.cottonclubjapan.co.jp/reserve/"
                          f"schedule/exec/x#{date}",
               title_ja="t", start_date=date, **overrides)
    return CottonClubScraper().parse_detail(html, ev)
