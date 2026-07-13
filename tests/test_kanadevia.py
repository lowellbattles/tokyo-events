"""Tests for the Kanadevia Hall scraper (旧 TOKYO DOME CITY HALL).

Fixture is raw HTML saved from the official schedule page
https://www.tokyo-dome.co.jp/tdc-hall/event/ (2026-07-13):
  kanadevia_hall_live.html — one static page carrying BOTH month tabs
  (2026年07月 + 2026年08月) inline. 62 calendar-day rows, 42 with content;
  41 are ticketed events, 1 is a "Reserved" hold (その他, no ticket link).

No detail fixture: title links are third-party promoter/ticketing sites
that the aggregator never scrapes, and everything stored (title, date,
type, OPEN/START) is already on this listing page (supports_detail=False).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.kanadevia import KanadeviaHallScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return {e.source_url: e
            for e in KanadeviaHallScraper().parse(
                _load("kanadevia_hall_live.html"))}


# ------------------------------------------------------------------- listing
def test_parses_every_ticketed_day_across_both_months():
    # 42 content rows over Jul+Aug; the single "Reserved" hold is dropped.
    evs = _events()
    assert len(evs) == 41
    assert KanadeviaHallScraper.supports_detail is False


def test_first_july_event_fields():
    e = _events()["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-01"]
    assert e.title_ja == "BOOM KYO PROTOSTAR -Produced by TOKYO SPACESHIP-"
    assert e.start_date == "2026-07-01"
    assert (e.open_time, e.start_time) == ("18:00", "19:00")  # 開場／開演
    assert e.category == Category.MUSIC
    assert e.venue_name == "Kanadevia Hall"
    assert e.venue_area == "Suidobashi"


def test_start_only_row_leaves_open_time_empty():
    # 2026-07-25 lists only "開演 17:30" (no 開場) -> start set, open None.
    e = _events()["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-25"]
    assert e.title_ja == "B&ZAI LIVE 2026 Summer Beat"
    assert e.open_time is None
    assert e.start_time == "17:30"


def test_second_month_tab_is_parsed_from_its_own_heading():
    # August rows come from the second c-mod-tab__body's own 2026年08月 heading.
    e = _events()["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-08-29"]
    assert e.title_ja == "ジュニア 夏祭り 2026"
    assert e.start_date == "2026-08-29"


# ------------------------------------------------------------------- URLs
def test_source_urls_are_internal_https_with_date_fragment():
    evs = _events()
    assert all(u.startswith(
        "https://www.tokyo-dome.co.jp/tdc-hall/event/#20") for u in evs)


def test_ticket_urls_are_absolute_https_third_party_links():
    evs = _events()
    assert all((e.ticket_url or "").startswith("https://")
               for e in evs.values())
    e01 = evs["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-01"]
    assert e01.ticket_url == "https://www.event-td.com/tokyospaceship/"


def test_multiday_run_days_are_distinct_events_sharing_one_ticket_url():
    # B&ZAI runs Jul 25 & 26 (different showtimes) -> two events, distinct
    # #date source_urls, but the SAME external ticket URL.
    evs = _events()
    d25 = evs["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-25"]
    d26 = evs["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-26"]
    assert d25.source_url != d26.source_url
    assert d25.ticket_url == d26.ticket_url
    assert d25.start_date == "2026-07-25" and d26.start_date == "2026-07-26"


# ------------------------------------------------------- category / policy
def test_reserved_hold_row_is_skipped():
    # 2026-07-03 is tagged その他 with plain-text "Reserved" and NO ticket
    # link -> a venue hold, not a public event; must not appear.
    evs = _events()
    assert "https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-03" not in evs
    assert not any(e.start_date == "2026-07-03" for e in evs.values())


def test_event_tagged_rows_stay_music():
    # イベント (artist/idol-led events at a music hall) are kept as MUSIC.
    evs = _events()
    lemon = evs["https://www.tokyo-dome.co.jp/tdc-hall/event/#2026-07-04"]
    assert lemon.tags == ["イベント"]
    assert lemon.category == Category.MUSIC
    # whole fixture is concerts + artist events -> all MUSIC
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
              <p class="c-txt-tag"><span class="c-txt-tag__item">コンサート</span></p>
              <p class="c-mod-calender__links">
                <a href="https://example.com/x">ディズニー・オン・アイス 2026</a></p>
              <p class="c-txt-caption-01">開場 12:00／開演 13:00</p>
            </div>
          </td>
        </tr>
      </tbody></table>
    </div>"""
    evs = KanadeviaHallScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
    assert evs[0].start_date == "2026-09-05"


def test_empty_html_yields_no_events_loud_failure():
    assert KanadeviaHallScraper().parse("<html></html>") == []
