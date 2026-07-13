import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.fever import FeverScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return FeverScraper().parse(_load("fever_shindaita_live.html"))


def _by_url_tail(evs, tail):
    return next(e for e in evs if e.source_url.endswith(tail))


def test_parses_every_entry_on_the_month_page():
    # 29 <div class="entry-asset"> blocks on the live 2026-07 schedule page.
    assert len(_events()) == 29


def test_first_event_fields_and_drink_surcharge_excluded():
    # 26.07.01 GEZAN screening. Door ￥2000, with a SEPARATE "※1drink ￥600"
    # line — the drink charge must NOT be read as the ticket price.
    e = _by_url_tail(_events(), "/schedule/2026/07/0119.html")
    assert e.title_ja == "GEZAN『Live at 武道館』DVD & Blu-ray 発売記念 爆音上映会"
    assert e.start_date == "2026-07-01"
    assert (e.open_time, e.start_time) == ("18:30", "19:00")
    assert e.price_min == 2000          # not 600 (the 1drink surcharge)
    assert e.lineup == []               # screening: no <h3> lineup block
    assert e.category == Category.MUSIC
    assert e.genres == []
    assert e.venue_name == "新代田FEVER" and e.venue_area == "Shindaita"


def test_lineup_times_price_soldout_and_eplus_link():
    # 26.07.02 TOKYO Hz — has an <h3> lineup, SOLD OUT, and an eplus link.
    e = _by_url_tail(_events(), "/schedule/2026/07/0218.html")
    assert e.title_ja == "TOKYO Hz 〜夢燦燦〜 #3"
    assert e.start_date == "2026-07-02"
    assert (e.open_time, e.start_time) == ("18:00", "18:30")
    assert e.price_min == 3500          # "ADV ￥3500 (+1drink)"
    assert e.lineup == ["the paddles", "Sunny Girl", "バチカン市国に愛されたい"]
    assert e.is_sold_out is True        # "THANK YOU SOLD OUT!!!"
    assert e.ticket_links == [
        {"provider": "eplus",
         "url": "https://eplus.jp/sf/venue/1560020/events", "code": None}
    ]


def test_opening_act_marker_dropped_but_guest_kept():
    # 26.07.03 — h3 has "CHRONOMETER / BRAHMAN" then "＜Opening Act＞ / ONE'S
    # TRUTH": the section marker is dropped, the opening act artist kept.
    e = _by_url_tail(_events(), "/schedule/2026/07/0319.html")
    assert e.lineup == ["CHRONOMETER", "BRAHMAN", "ONE'S TRUTH"]


def test_streaming_ticket_price_not_mistaken_for_door_price():
    # 26.07.19 — venue price ADV ￥7000 / DOOR ￥7500, plus a cheaper ￥3000
    # STREAMING ticket in a later block (no ADV/DOOR keyword). Door price wins.
    e = _by_url_tail(_events(), "/schedule/2026/07/1916.html")
    assert e.price_min == 7000          # not 3000 (the 配信 streaming ticket)
    # livepocket uses the bare domain (textutils only knows t.livepocket.jp),
    # so the scraper's own domain map must still capture it.
    providers = {t["provider"] for t in e.ticket_links}
    assert providers == {"livepocket", "eplus"}


def test_zero_yen_event_is_marked_free():
    # 26.07.16 FREECY vol.2 — "ADV ￥0 (+2drink)".
    e = next(e for e in _events() if e.title_ja == "FREECY vol.2")
    assert e.price_min == 0
    assert e.is_free is True


def test_detail_urls_absolute_https_on_venue_domain_and_unique():
    evs = _events()
    assert all(
        e.source_url.startswith("https://www.fever-popo.com/schedule/")
        and e.source_url.endswith(".html")
        for e in evs
    )
    # one detail page per event -> dedupe by source_url keeps all 29.
    assert len({e.source_url for e in evs}) == 29


def test_all_rows_are_music():
    # Pure indie live house — every real row is a concert.
    assert {e.category for e in _events()} == {Category.MUSIC}


def test_empty_html_yields_nothing_loud_failure():
    assert FeverScraper().parse("<html></html>") == []
    assert FeverScraper().parse("") == []


def test_nonmusic_row_downgraded_to_other():
    # FEVER never books sumo, but the tu.is_nonmusic guard must still downgrade
    # an obvious non-concert row to OTHER rather than mislabel it as music.
    html = (
        '<div class="entry-asset asset hentry">'
        '<div class="asset-header">'
        '<h2 class="eventtitle">26.07.15 (Tue)　大相撲 特別公演</h2>'
        '<meta property="og:title" content="大相撲 特別公演" />'
        '<meta property="og:url" '
        'content="https://www.fever-popo.com/schedule/2026/07/1512.html" />'
        '</div><div class="asset-content entry-content"><div class="asset-body">'
        '<div>OPEN 12:00 / START 13:00</div>'
        '</div></div></div>'
    )
    evs = FeverScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER
    assert evs[0].start_date == "2026-07-15"
