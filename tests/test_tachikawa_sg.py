import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.tachikawa_sg import TachikawaStageGardenScraper

FIX = Path(__file__).parent / "fixtures"
# The listing rows carry MM/DD but no year, so pin the page month exactly as
# scrape() would (this fixture is the July 2026 /events/ page).
JULY = dt.date(2026, 7, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = TachikawaStageGardenScraper().parse(
        _load("tachikawa_stage_garden_live.html"), month=JULY)
    # key by the zero-padded event id in the detail URL
    return {e.source_url.rsplit("/", 1)[-1].replace(".php", ""): e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    # 20 calendar-day rows in the fixture; 3 are "Reserved" (no link) ->
    # 13 real event rows, one Event each.
    assert len(_july()) == 13


def test_field_spotchecks_from_fixture():
    ev = _july()
    # ばってん少女隊11周年記念ライブ, 07/05, 16:00開場／17:00開演
    b = ev["00001373"]
    assert b.title_ja == "ばってん少女隊11周年記念ライブ"
    assert (b.start_date, b.open_time, b.start_time) == \
        ("2026-07-05", "16:00", "17:00")
    assert b.category == Category.MUSIC
    # GNJB 7th Anniversary 原因は君にもある。, 07/07, numbered shows ①14:30/15:30
    # ②18:00/19:00 -> record the EARLIEST performance's times.
    g = ev["00001443"]
    assert g.title_ja == "GNJB 7th Anniversary 原因は君にもある。"
    assert (g.start_date, g.open_time, g.start_time) == \
        ("2026-07-07", "14:30", "15:30")
    # SkyPeace 10th Anniversary 祝笑祭, 07/17, 18:00開場／19:00開演
    s = ev["00001471"]
    assert s.title_ja == "SkyPeace 10th Anniversary 祝笑祭"
    assert (s.start_date, s.open_time, s.start_time) == \
        ("2026-07-17", "18:00", "19:00")


def test_absolute_detail_urls_and_venue_meta():
    ev = _july()
    assert ev["00001373"].source_url == \
        "https://www.t-sg.jp/events/2026/07/00001373.php"
    for e in ev.values():
        assert e.source_url.startswith("https://www.t-sg.jp/events/")
        assert e.source_url.endswith(".php")
        assert e.venue_name == "TACHIKAWA STAGE GARDEN（立川ステージガーデン）"
        assert e.venue_area == "Tachikawa"
        assert e.genres == []          # tagging happens at export


def test_year_is_injected_from_page_month_not_hardcoded():
    # Same fixture, a different pinned page year -> the year follows the page.
    evs = TachikawaStageGardenScraper().parse(
        _load("tachikawa_stage_garden_live.html"), month=dt.date(2027, 7, 1))
    by = {e.source_url.rsplit("/", 1)[-1].replace(".php", ""): e for e in evs}
    assert by["00001373"].start_date == "2027-07-05"


def test_category_policy_mixed_calendar():
    ev = _july()
    # The corporate job fair (data-genre="企業説明会", title contains 企業説明会
    # which matches the shared 説明会 keyword) must be OTHER.
    fair = ev["00001492"]
    assert "企業説明会" in fair.title_ja
    assert fair.category == Category.OTHER
    assert fair.tags == ["企業説明会"]     # the site's own type label is kept
    # The dance CHAMPIONSHIP is tagged ダンス by the site; the shared keyword
    # set doesn't cover it, so it stays MUSIC (precision-first). Documents the
    # policy so a future broadening is a conscious choice.
    dance = ev["00001473"]
    assert dance.tags == ["ダンス"]
    assert dance.category == Category.MUSIC
    # Every other booked row here is a concert -> MUSIC.
    music = [e for k, e in ev.items() if k not in ("00001492",)]
    assert all(e.category == Category.MUSIC for e in music)


def test_reserved_days_and_promoter_line_are_not_events():
    ev = _july()
    # "Reserved" rows have no detail link -> not parsed. And the listing's
    # trailing promoter/contact <p> is NOT mistaken for lineup.
    assert all(e.lineup == [] for e in ev.values())
    # 07/03, 07/04, 07/06, 07/24 are Reserved -> none of those days appear.
    dates = {e.start_date for e in ev.values()}
    for reserved in ("2026-07-03", "2026-07-04", "2026-07-06", "2026-07-24"):
        assert reserved not in dates


def test_empty_html_returns_nothing_loudly():
    assert TachikawaStageGardenScraper().parse("<html></html>", month=JULY) == []
    assert TachikawaStageGardenScraper().parse("", month=JULY) == []


# ------------------------------------------------------------------ detail
def test_parse_detail_yen_suffix_prices_and_performers():
    # Detail prices are written "N,NNN円" (円 suffix, no ¥) which the generic
    # ¥-keyed parser misses; performers come from the 出演者 row.
    ev = Event(source="tachikawa_stage_garden",
               source_url="https://www.t-sg.jp/events/2026/07/00001373.php",
               title_ja="ばってん少女隊11周年記念ライブ",
               category=Category.MUSIC, start_date="2026-07-05")
    TachikawaStageGardenScraper().parse_detail(
        _load("tachikawa_stage_garden_detail.html"), ev)
    # 公演日時 row: 16:00開場／17:00開演 (fills the empty times)
    assert (ev.open_time, ev.start_time) == ("16:00", "17:00")
    # 出演者 row
    assert ev.lineup == ["ばってん少女隊"]
    # 料金 row: 15,000 (premium) / 9,000 (一般) / 5,000 (U-22) -> min 5,000
    assert ev.price_min == 5000
    assert ev.is_free is False
    assert ev.price_text and "15,000円" in ev.price_text
    # The only outbound link is the artist's 公式サイト (battengirls.com), not a
    # playguide -> no ticket links picked up.
    assert ev.ticket_links == []
