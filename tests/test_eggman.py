import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.eggman import EggmanScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _day():
    return EggmanScraper().parse(_load("eggman_live.html"))


def _night():
    return EggmanScraper().parse(_load("eggman_nighttime_live.html"))


def _by_title(evs, needle):
    return next(e for e in evs if needle in (e.title_ja or ""))


def test_daytime_parses_every_article():
    # 17 <article class="scheduleList"> blocks on the 2026-07 DAY TIME page.
    assert len(_day()) == 17


def test_nighttime_parses_every_article():
    # 4 blocks on the 2026-07 NIGHT TIME page.
    assert len(_night()) == 4


def test_first_daytime_event_fields():
    # Date is the page month header "2026.07" combined with <time><strong>03.
    e = _by_title(_day(), "丸山純奈 エッグマンスリーLIVE")
    assert e.title_ja == "「丸山純奈 エッグマンスリーLIVE vol.3」"
    assert e.start_date == "2026-07-03"
    assert (e.open_time, e.start_time) == ("18:30", "19:00")
    assert e.price_min == 4400            # "ADV 4400yen+1D" (no ¥ symbol)
    assert e.lineup == ["丸山純奈"]
    assert e.category == Category.MUSIC
    assert e.genres == []
    assert e.venue_name == "Shibuya eggman" and e.venue_area == "Shibuya"
    assert e.source_url == "http://eggman.jp/schedule/schedule-31627/"


def test_freeform_price_without_yen_symbol():
    # li.other holds "一般3,400 / 学生2,400": bare comma-grouped numbers, no ¥.
    e = _by_title(_day(), "Junk days・Love vol.19")
    assert e.start_date == "2026-07-30"
    assert e.price_min == 2400
    assert e.price_text == "一般3,400 / 学生2,400"


def test_yen_symbol_price_takes_cheapest_tier():
    # "全自由：¥5,500 / U-22：¥3,500" -> min 3500.
    e = _by_title(_day(), "10th Anniversary Live Tour 2026")
    assert e.start_date == "2026-07-05"
    assert e.price_min == 3500
    assert e.lineup == ["Blue Vintage"]


def test_artist_and_comedian_group_labels_stripped():
    # act block: 【ARTIST】 ... / 【COMEDIAN】 ... — labels dropped, both
    # groups merged into one lineup.
    e = _by_title(_day(), "Junk days・Love vol.18")
    assert e.start_date == "2026-07-06"
    assert e.lineup == [
        "栢本ての(trio set)", "キッサ・コッポラ", "砂の壁", "稀",
        "YABI×YABI", "パンプキンポテトフライ", "惹女香花",
    ]
    assert e.price_min == 2400            # "一般¥2,900 / 学生¥2,400"


def test_detail_urls_are_absolute_http_on_venue_domain():
    evs = _day()
    assert all(e.source_url.startswith("http://eggman.jp/schedule/")
               for e in evs)
    # slugs are unique -> dedupe by source_url keeps all 17.
    assert len({e.source_url for e in evs}) == 17


def test_nighttime_late_start_and_tba():
    # Club night: OPEN 24:00, START is "TBA" (no time) -> start_time None.
    e = _by_title(_night(), "HEAVY SMOKERZ FOREST")
    assert e.start_date == "2026-07-10"
    assert e.open_time == "24:00"
    assert e.start_time is None
    assert e.price_min == 3000            # "TICKET 3000yen+1D"
    assert e.lineup[0] == "LIL’BCCNo"


def test_nighttime_entry_site_link_and_bare_price():
    # act block is just an "ENTRY SITE" reservation link -> captured as a
    # ticket link, NOT treated as an artist. Price "3000+1D / 1500+1D" are
    # bare integers (no ¥, no comma, no yen suffix).
    e = _by_title(_night(), "SELLOUT 2026")
    assert e.start_date == "2026-07-23"
    assert (e.open_time, e.start_time) == ("23:30", "24:30")
    assert e.price_min == 1500
    assert e.lineup == []
    assert e.ticket_links == [
        {"provider": "other",
         "url": "https://et-stage.net/event/NS8xMjcxNw/", "code": None}
    ]


def test_empty_html_yields_nothing_loud_failure():
    assert EggmanScraper().parse("<html></html>") == []
    assert EggmanScraper().parse("") == []


def test_nonmusic_row_downgraded_to_other():
    # eggman is a pure live house, so this never fires on the real feed, but
    # the tu.is_nonmusic guard must still downgrade an obvious non-concert row.
    html = (
        '<div class="monthHeader"><h1>2026.07</h1></div>'
        '<article class="scheduleList"><div class="sheListLeft">'
        '<div class="scheListHeader">'
        '<time><strong>15</strong><small>TUE</small></time>'
        '<h1><a href="http://eggman.jp/schedule/x/">大相撲 特別公演</a></h1>'
        '</div><div class="scheListBody"><ul>'
        '<li><small>OPEN</small> 12:00</li></ul>'
        '<div class="act"><p>-</p></div></div></div></article>'
    )
    evs = EggmanScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
