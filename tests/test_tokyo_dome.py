"""Tests for the Tokyo Dome scraper (東京ドーム, ~55,000-cap stadium).

Fixture is raw HTML saved from the official schedule page
https://www.tokyo-dome.co.jp/dome/event/schedule.html (2026-07-13):
  tokyo_dome_live.html — one static page carrying a FULL YEAR of month
  tabs inline (2026年07月 … 2027年02月, 7 confirmed-event months; October
  2026 is absent = no confirmed events). Across the year there are 35
  コンサート performance-day rows; the rest are 野球 (Giants games /
  TOKYO DOME TOUR) and non-concert イベント rows a music aggregator drops.

No detail fixture: title links are third-party artist/promoter sites the
aggregator never scrapes, and everything stored (title, date, type,
OPEN/START) is already on this listing page (supports_detail=False).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.tokyo_dome import TokyoDomeScraper

FIX = Path(__file__).parent / "fixtures"
SCHED = "https://www.tokyo-dome.co.jp/dome/event/schedule.html"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.source_url: e
            for e in TokyoDomeScraper().parse(_load("tokyo_dome_live.html"))}


# ------------------------------------------------------------------- listing
def test_parses_every_concert_performance_day():
    # 35 コンサート day-instances across the year; baseball/event rows out.
    evs = _events()
    assert len(evs) == 35
    assert TokyoDomeScraper.supports_detail is False


def test_first_concert_fields():
    # 2026-07-04 TOP4 (Saturday show): 開場 15:30／開演 17:30.
    e = _events()[f"{SCHED}#2026-07-04"]
    assert e.title_ja == "TOP4 in TOKYO DOME 2Days"
    assert e.start_date == "2026-07-04"
    assert (e.open_time, e.start_time) == ("15:30", "17:30")
    assert e.category == Category.MUSIC
    assert e.ticket_url == "https://www.top4-event.com/"


def test_venue_metadata():
    e = _events()[f"{SCHED}#2026-07-04"]
    assert e.venue_name == "東京ドーム"
    assert e.venue_area == "Suidobashi"
    assert e.address == "1-3-61 Koraku, Bunkyo-ku, Tokyo (Tokyo Dome City)"
    assert (e.lat, e.lng) == (35.70558, 139.75195)


def test_later_month_tab_parsed_from_own_heading():
    # December sits in a later c-mod-tab__body with its own 2026年12月 heading.
    e = _events()[f"{SCHED}#2026-12-05"]
    assert e.title_ja == "YOASOBI ASIA 10-CITY DOME & STADIUM TOUR 2026-2027"
    assert e.start_date == "2026-12-05"
    assert (e.open_time, e.start_time) == ("15:30", "18:00")
    assert e.ticket_url == "https://www.yoasobi-music.jp/live/54776"


def test_start_only_row_leaves_open_time_empty():
    # 2026-07-25 Ryosuke Yamada lists only "開演 18:00" (no 開場).
    e = _events()[f"{SCHED}#2026-07-25"]
    assert e.title_ja == "Ryosuke Yamada DOME TOUR 2026 Are You Red.Y?"
    assert e.open_time is None
    assert e.start_time == "18:00"


def test_multiday_run_days_are_distinct_events_sharing_one_ticket_url():
    # TOP4 runs Jul 4 & 5 with DIFFERENT showtimes -> two events, distinct
    # #date source_urls, same external ticket URL.
    evs = _events()
    d4 = evs[f"{SCHED}#2026-07-04"]
    d5 = evs[f"{SCHED}#2026-07-05"]
    assert d4.source_url != d5.source_url
    assert d4.ticket_url == d5.ticket_url == "https://www.top4-event.com/"
    assert (d4.start_time, d5.start_time) == ("17:30", "17:00")


def test_confirmed_concert_without_ticket_link_is_kept():
    # ＝LOVE (idol) is a コンサート row with a plain-text title and NO anchor
    # (tickets not yet on sale) — kept as a fact with ticket_url=None.
    e = _events()[f"{SCHED}#2027-01-19"]
    assert e.title_ja == "＝LOVE in TOKYO DOME"
    assert e.ticket_url is None
    assert e.category == Category.MUSIC


# ------------------------------------------------------------------- URLs
def test_source_urls_are_internal_https_with_date_fragment():
    evs = _events()
    assert all(u.startswith(f"{SCHED}#20") for u in evs)


def test_ticket_urls_when_present_are_absolute_https_third_party():
    tickets = [e.ticket_url for e in _events().values() if e.ticket_url]
    assert len(tickets) >= 30            # only ＝LOVE lacks a link so far
    assert all(t.startswith("https://") for t in tickets)
    assert all("tokyo-dome.co.jp" not in t for t in tickets)


# ------------------------------------------------------- category / policy
def test_baseball_and_dome_tour_rows_are_skipped():
    # 野球 rows (Giants games + TOKYO DOME TOUR) are the stadium's own
    # business, never public music events -> absent from output entirely.
    evs = _events()
    assert not any("野球" in (e.tags or []) for e in evs.values())
    titles = [e.title_ja for e in evs.values()]
    assert not any("巨人" in t for t in titles)
    assert not any("TOKYO DOME TOUR" in t for t in titles)
    # 2026-07-07 is a baseball-only day -> no event emitted for it.
    assert f"{SCHED}#2026-07-07" not in evs


def test_all_concert_rows_tagged_concert_and_music():
    evs = _events()
    assert {tuple(e.tags) for e in evs.values()} == {("コンサート",)}
    assert {e.category for e in evs.values()} == {Category.MUSIC}


def test_nonmusic_title_forces_other_even_under_concert_tag():
    # Safety net: a clearly non-concert title (ice show) is OTHER regardless
    # of the site's コンサート tag.
    html = """
    <div class="c-mod-tab__body">
      <p class="c-ttl-set-calender">2026年09月</p>
      <table class="c-mod-calender"><tbody>
        <tr class="c-mod-calender__item">
          <th class="c-mod-calender__title">
            <span class="c-mod-calender__day">05</span>
            <span class="c-mod-calender__day">(土)</span></th>
          <td class="c-mod-calender__detail">
            <div class="c-mod-calender__detail-in">
              <div class="c-mod-calender__detail-col">
                <p class="c-txt-tag"><span class="c-txt-tag__item">コンサート</span></p>
              </div>
              <div class="c-mod-calender__detail-col">
                <p class="c-mod-calender__links">
                  <a href="https://example.com/x">ディズニー・オン・アイス 2026</a></p>
                <p class="c-txt-caption-01">開場 12:00／開演 13:00</p>
              </div>
            </div>
          </td>
        </tr>
      </tbody></table>
    </div>"""
    evs = TokyoDomeScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
    assert evs[0].start_date == "2026-09-05"


def test_empty_html_yields_no_events_loud_failure():
    assert TokyoDomeScraper().parse("<html></html>") == []
