import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.seata import SeataScraper, _parse_price

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return SeataScraper().parse(_load("club_seata_live.html"))


def _by_id(evs, detail_id):
    return next(e for e in evs
               if e.source_url.rsplit("/", 1)[1] == detail_id)


# --- count ---------------------------------------------------------------
def test_parses_every_calendar_event():
    # 25 <a class="schedule"> across the July 2026 calendar grid, including two
    # days (19th, 28th) that hold two distinct events each.
    assert len(_events()) == 25


def test_all_source_urls_unique():
    evs = _events()
    assert len({e.source_url for e in evs}) == 25


# --- field spot-checks ---------------------------------------------------
def test_first_event_fields():
    e = _by_id(_events(), "46860")
    assert e.title_ja == "Minillon vol.5"
    assert e.start_date == "2026-07-01"                 # from data-date="20260701"
    assert e.source_url == "https://www.seata.jp/schedule/detail/46860"
    assert e.category == Category.MUSIC
    assert e.genres == []
    # listing carries no times/prices (they are commented-out fakes) -> gaps.
    assert (e.open_time, e.start_time, e.price_min) == (None, None, None)
    assert e.venue_name == "吉祥寺CLUB SEATA"
    assert e.venue_area == "Kichijoji"
    assert e.address.startswith("〒180-0004")
    assert (e.lat, e.lng) == (35.70575, 139.57962)


def test_html_entities_decoded_in_title():
    # raw grid text is `&quot;SOUND ENERGY&quot;  -BAND SET 2MAN LIVE-`
    e = _by_id(_events(), "47218")
    assert e.title_ja == '"SOUND ENERGY" -BAND SET 2MAN LIVE-'
    assert e.start_date == "2026-07-09"


def test_non_latin_title_preserved():
    e = _by_id(_events(), "52021")
    assert e.title_ja == "여기여기 (YogiYogi)vol.34"
    assert e.start_date == "2026-07-03"


def test_two_events_on_one_day_both_kept():
    evs = _events()
    a = _by_id(evs, "54560")
    b = _by_id(evs, "46375")
    assert a.start_date == b.start_date == "2026-07-19"
    assert "WONDER WORLD" in a.title_ja
    assert "GIRLS WONDER" in b.title_ja


# --- detail-url join -----------------------------------------------------
def test_detail_urls_are_absolute_https_on_venue_domain():
    evs = _events()
    assert all(
        e.source_url.startswith("https://www.seata.jp/schedule/detail/")
        for e in evs)


# --- loud failure --------------------------------------------------------
def test_empty_html_yields_nothing():
    assert SeataScraper().parse("<html></html>") == []
    assert SeataScraper().parse("") == []


# --- category policy -----------------------------------------------------
def test_nonmusic_row_downgraded_to_other():
    # SEATA is a pure live house, so this never fires on the real feed, but the
    # tu.is_nonmusic guard must still downgrade an obvious non-concert title.
    html = (
        '<table class="calendar"><tbody><tr>'
        '<td data-date="20260715"><div class="day">'
        '<div class="day_num">15</div>'
        '<a href="https://www.seata.jp/schedule/detail/99999" '
        'class="schedule">大相撲 特別公演</a>'
        '</div></td></tr></tbody></table>'
    )
    evs = SeataScraper().parse(html)
    assert len(evs) == 1
    assert evs[0].category == Category.OTHER


# --- detail enrichment ---------------------------------------------------
def test_parse_detail_times_and_price():
    ev = Event(
        source="club_seata",
        source_url="https://www.seata.jp/schedule/detail/46860",
        title_ja="Minillon vol.5", start_date="2026-07-01")
    ev = SeataScraper().parse_detail(_load("club_seata_detail_live.html"), ev)
    # <dd>OPEN15:10/START15:30</dd> (no spaces/colons after OPEN/START)
    assert (ev.open_time, ev.start_time) == ("15:10", "15:30")
    # "ADV/DOOR ￥3,500/￥4,500 (1Drink代金￥700別途必要)" -> 3500, NOT the ￥700 fee
    assert ev.price_min == 3500
    assert ev.is_free is False
    assert "ADV/DOOR" in ev.price_text
    # this event's ticketBnr is empty on the live page
    assert ev.ticket_links == []
    assert ev.is_sold_out is False


def test_price_helper_strips_drink_surcharge():
    # the trailing "(1Drink代金￥700別途必要)" must never win the cheapest tier
    assert _parse_price(
        "ADV/DOOR ￥3,500/￥4,500 (1Drink代金￥700別途必要)")[1] == 3500
    # bare "円" amounts (no ¥ symbol) are read; cheapest general tier wins
    assert _parse_price(
        "■前方優先エリア前売り3500円/当日4500円 , "
        "■一般前売り2500円/当日3500円 (各1Drink代金￥700別途必要)")[1] == 2500
    # multi-tier ¥ list, drink note outside parens (※各1D別) has no amount
    assert _parse_price("各 VIP ticket ¥5,000 N ticket ¥1,000 ※各1D別")[1] == 1000
    # ￥700 with drink INCLUDED (1D込み) is a genuine ￥700 ticket, kept
    assert _parse_price("前売：￥700（1D込み）")[1] == 700
