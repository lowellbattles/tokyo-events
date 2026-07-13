import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.veats import VeatsScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    # month is derived from the page's own this-month nav (param=202607),
    # so no month kwarg is needed — but tests pin it for clarity.
    return VeatsScraper().parse(_load("veats_shibuya_live.html"),
                                month=dt.date(2026, 7, 1))


def _by_id(evs, num):
    return next(e for e in evs if e.source_url.endswith(f"/schedule/{num}/"))


def test_parses_every_event_block():
    # 30 <a class="today-lists"> blocks on the 2026-07 page.
    assert len(_july()) == 30


def test_month_derived_from_page_nav_without_kwarg():
    # parse() must self-resolve YYYYMM from the this-month link.
    evs = VeatsScraper().parse(_load("veats_shibuya_live.html"))
    assert len(evs) == 30
    assert _by_id(evs, 8342).start_date == "2026-07-01"


def test_first_event_fields():
    e = _by_id(_july(), 8342)
    assert e.title_ja == "CENT / Aile The Shota / Klang Ruler / ちゃんぴおんず"
    assert e.subtitle == "THAT is YOUTH!!!!FES vol.3"
    assert e.start_date == "2026-07-01"
    assert (e.open_time, e.start_time) == ("18:00", "19:00")
    assert e.lineup == ["CENT", "Aile The Shota", "Klang Ruler", "ちゃんぴおんず"]
    assert e.category == Category.MUSIC
    assert e.genres == []
    assert e.venue_name == "Veats Shibuya" and e.venue_area == "Shibuya"
    assert e.source_url == "https://veats.jp/schedule/8342/"


def test_two_set_day_takes_first_sets_times():
    # id 8252: "【1部】 15:00 / 15:30 / 【2部】18:30 / 19:00" -> first set.
    e = _by_id(_july(), 8252)
    assert e.title_ja == "SURFACE / 中島卓偉"
    assert e.subtitle == "さぁ!大器晩成!"
    assert (e.open_time, e.start_time) == ("15:00", "15:30")


def test_tba_times_yield_none():
    # id 8394: OPEN/START cell is "TBA / TBA".
    e = _by_id(_july(), 8394)
    assert e.start_date == "2026-07-07"
    assert (e.open_time, e.start_time) == (None, None)


def test_sold_out_flag_from_listing():
    # id 8508 (day 09) carries <strong class="soldout">SOLD OUT</strong>.
    e = _by_id(_july(), 8508)
    assert e.is_sold_out is True
    # a normal row is not flagged
    assert _by_id(_july(), 8342).is_sold_out is False


def test_event_name_as_headline_keeps_artists_in_lineup():
    # id 8516: here the headline (p.ttl) is the event name and the artists
    # sit in the LINE UP row.
    e = _by_id(_july(), 8516)
    assert e.title_ja == "「'@JAM CONNECT 〜ROAD TO @JAM EXPO 2026 敗者復活LIVE〜」"
    assert "InnocentFairy" in e.lineup and "來未あい" in e.lineup


def test_detail_urls_are_absolute_https_on_venue_domain():
    evs = _july()
    assert all(e.source_url.startswith("https://veats.jp/schedule/")
               for e in evs)
    assert len({e.source_url for e in evs}) == 30


def test_parse_detail_fills_price_and_ticket_link():
    e = _by_id(_july(), 8342)          # a real listing Event for 8342 …
    VeatsScraper().parse_detail(_load("veats_shibuya_detail.html"), e)
    assert e.price_min == 6500         # "ADV / DOOR  ¥6,500 / ¥7,000 (D代別)"
    assert "6,500" in (e.price_text or "")
    assert e.ticket_links == [
        {"provider": "other",
         "url": "https://centplanet.jp/news/detail/942", "code": None},
    ]
    # listing already set the times -> detail must not clobber them
    assert (e.open_time, e.start_time) == ("18:00", "19:00")


def test_empty_html_yields_nothing_loud_failure():
    assert VeatsScraper().parse("<html></html>") == []
    assert VeatsScraper().parse("") == []


def test_nonmusic_row_downgraded_to_other():
    # Veats is a pure live house so this never fires on the real feed, but the
    # tu.is_nonmusic guard must still downgrade an obvious non-concert row.
    html = (
        '<li class="this-month"><a href="/schedule/?param=202607">07</a></li>'
        '<a href="https://veats.jp/schedule/9999/" class="today-lists">'
        '<p class="day">15<span class="date">TUE</span></p>'
        '<p class="ttl text-overhidden4">プロレス 特別興行</p>'
        '<p class="text-overhidden5">興行タイトル</p>'
        '<dl class="open-start"><dt>OPEN/START</dt><dd>17:00 / 18:00</dd></dl>'
        '<dl><dt>LINE UP</dt><dd class="one-column">-</dd></dl>'
        '</a>'
    )
    evs = VeatsScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
