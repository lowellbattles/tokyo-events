import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.garden_theater import GardenTheaterScraper

FIX = Path(__file__).parent / "fixtures"
# Listing rows carry MM/DD but no year, so pin the page month exactly as
# scrape() would. These fixtures are the July / August 2026 schedule pages.
JULY = dt.date(2026, 7, 1)
AUG = dt.date(2026, 8, 1)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _id(ev):
    return ev.source_url.rstrip("/").rsplit("/", 1)[-1]


def _july():
    evs = GardenTheaterScraper().parse(
        _load("tokyo_garden_theater_live.html"), month=JULY)
    return {_id(e): e for e in evs}


def _aug():
    evs = GardenTheaterScraper().parse(
        _load("tokyo_garden_theater_live_aug.html"), month=AUG)
    return {_id(e): e for e in evs}


# ----------------------------------------------------------------- listing
def test_parses_exact_count():
    assert len(_july()) == 9      # 9 event <li> on the July page
    assert len(_aug()) == 12      # 11 concerts + 1 大会イベント on August


def test_field_spotchecks_from_fixture():
    ev = _july()
    # id 3968 — キタニタツヤ, single day 07/17
    k = ev["3968"]
    assert k.title_ja.startswith("TATSUYA KITANI Presents")
    assert "amazarashi" in k.title_ja
    assert k.start_date == "2026-07-17"
    assert k.end_date is None
    assert k.category == Category.MUSIC
    assert k.lineup == ["キタニタツヤ"]
    # id 5071 — DIR EN GREY, two-day run 07/18–07/19
    d = ev["5071"]
    assert d.title_ja == "DIR EN GREY MORTAL DOWNER"
    assert (d.start_date, d.end_date) == ("2026-07-18", "2026-07-19")
    assert d.lineup == ["DIR EN GREY"]
    # id 4561 — title carries an inline <br> that must become a space
    assert ev["4561"].title_ja == "Saucy Dog ONEMAN LIVE 2026 「NOW LOADING…」"


def test_detail_urls_absolute_and_venue_meta():
    ev = _july()
    assert ev["3968"].source_url == (
        "https://www.shopping-sumitomo-rd.com/tokyo_garden_theater/schedule/3968/")
    for e in ev.values():
        assert e.source_url.startswith(
            "https://www.shopping-sumitomo-rd.com/tokyo_garden_theater/schedule/")
        assert e.source_url.endswith("/")
        assert e.venue_name == "東京ガーデンシアター"
        assert e.venue_area == "Ariake"
        assert e.address == "東京都江東区有明2-1-6"
        assert e.genres == []          # genre tagging happens at export


def test_multiday_run_spanning_month_boundary():
    # id 4586 runs 07/31 -> 08/02; the second .ymd block is in August, so the
    # end date's year must follow the closest-to-page-month rule, not the
    # first block's month.
    assert (_july()["4586"].start_date, _july()["4586"].end_date) == \
        ("2026-07-31", "2026-08-02")
    # The same booking is also listed on the August page (its end month);
    # parsing that page must yield identical dates -> stable source_url dedupe.
    assert (_aug()["4586"].start_date, _aug()["4586"].end_date) == \
        ("2026-07-31", "2026-08-02")


def test_year_injected_from_page_month_not_hardcoded():
    evs = GardenTheaterScraper().parse(
        _load("tokyo_garden_theater_live.html"), month=dt.date(2027, 7, 1))
    by = {_id(e): e for e in evs}
    assert by["3968"].start_date == "2027-07-17"


def test_category_policy_mixed_calendar():
    ev = _aug()
    # id 5218 — Yu-Gi-Oh! WORLD CHAMPIONSHIP: the site tags it 大会イベント
    # (its li class is NOT event_concert), so it must be OTHER while kept.
    champ = ev["5218"]
    assert "WORLD CHAMPIONSHIP" in champ.title_ja
    assert champ.category == Category.OTHER
    assert champ.tags == ["大会イベント"]        # the site's own label is kept
    # A regular concert row stays MUSIC.
    assert ev["4676"].category == Category.MUSIC
    # Every other August row is tagged コンサート・ショー -> MUSIC.
    others = [e for k, e in ev.items() if k != "5218"]
    assert all(e.category == Category.MUSIC for e in others)
    assert all(e.tags == ["コンサート・ショー"] for e in others)


def test_empty_html_returns_nothing_loudly():
    s = GardenTheaterScraper()
    assert s.parse("<html></html>", month=JULY) == []
    assert s.parse("", month=JULY) == []


# ------------------------------------------------------------------ detail
def test_parse_detail_jp_open_close_prices_and_ticket_links():
    # The detail page writes times as 【開場】/【開演】 and prices as
    # ・<席種> ￥N,NNN(税込) — conventions the generic English OPEN/START,
    # ADV/前売 enrichment does not catch, hence the custom parse_detail.
    ev = Event(
        source="tokyo_garden_theater",
        source_url=("https://www.shopping-sumitomo-rd.com/"
                    "tokyo_garden_theater/schedule/3968/"),
        title_ja="TATSUYA KITANI Presents",
        category=Category.MUSIC, start_date="2026-07-17")
    GardenTheaterScraper().parse_detail(
        _load("tokyo_garden_theater_detail.html"), ev)
    assert (ev.open_time, ev.start_time) == ("17:30", "18:30")
    # Two ￥9,800(税込) tiers -> min 9,800, not free.
    assert ev.price_min == 9800
    assert ev.is_free is False
    assert ev.price_text and "9,800" in ev.price_text
    # Only playguide button is Lawson (l-tike.com); the official-site and
    # organizer links must not be mistaken for ticket links.
    assert ev.ticket_links == [
        {"provider": "lawson", "url": "https://l-tike.com/tatsuyakitani/",
         "code": None}]
    assert ev.is_sold_out is False
