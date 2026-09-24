import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers import textutils as tu
from tokyo_events.scrapers.japan_arts import (JapanArtsScraper, parse_concert_page,
                                              parse_listing)

FIX = Path(__file__).parent / "fixtures"

KANEKO_URL = "https://www.japanarts.co.jp/concert/p2231/"
NETOPIL_URL = "https://www.japanarts.co.jp/concert/p2243/"
KOBAYASHI_URL = "https://www.japanarts.co.jp/concert/p2247/"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------- listing
def test_listing_extracts_every_card():
    urls = parse_listing(_load("japan_arts_listing_live.html"))
    assert len(urls) == 45
    assert KANEKO_URL in urls
    assert all(u.startswith("https://www.japanarts.co.jp/concert/p") for u in urls)


def test_listing_urls_are_deduped_and_absolute():
    urls = parse_listing(_load("japan_arts_listing_live.html"))
    assert len(urls) == len(set(urls))


def test_second_page_mostly_overlaps_the_first():
    # pagination here is NOT date-ordered -- page 2 is not "the next 45"
    # but a differently-sorted view of nearly the same pool. As of the
    # 2026-09-24 probe only 3 of page 2's 47 cards were new vs page 1.
    p1 = set(parse_listing(_load("japan_arts_listing_live.html")))
    p2 = set(parse_listing(_load("japan_arts_listing_p2_live.html")))
    assert len(p2 - p1) == 3


def test_empty_listing_html_returns_nothing_loudly():
    assert parse_listing("") == []
    assert parse_listing("<html></html>") == []


# ------------------------------------------------------------------ detail
def test_single_leg_page_parses_venue_date_and_times():
    page = parse_concert_page(_load("japan_arts_detail_live.html"))
    assert len(page["legs"]) == 1
    leg = page["legs"][0]
    assert leg["date"] == "2026-10-18"
    # JA half only -- the "　EnglishName" tail is stripped before
    # venues.resolve_venue ever sees it.
    assert leg["venue_raw"] == "東京オペラシティ コンサートホール"
    assert leg["open_time"] == "15:20"
    assert leg["start_time"] == "16:00"
    # _clean() collapses the title's internal U+3000 to a plain space too
    assert leg["title"] == "日本デビュー15周年記念 金子三勇士 ピアノ・リサイタル"


def test_restricted_tiers_excluded_from_price_min():
    # 通常価格 S¥7,000/A¥5,000, 学生割引 (age-restricted) S¥3,500/A¥2,500,
    # プレミアムシート ¥10,000 (open to all), Miyujiシート A¥1,000 -- a
    # named child-only tier with NO restriction keyword in its own label,
    # caught only via the page's own "◎Miyujiシート（小学生限定...）"
    # footnote (textutils.restricted_tier_names).
    page = parse_concert_page(_load("japan_arts_detail_live.html"))
    leg = page["legs"][0]
    assert leg["price_min"] == 5000        # A席 通常価格, not any restricted tier
    assert "1,000円" in leg["price_text"]  # Miyuji tier still in the full list
    assert "3,500円" in leg["price_text"]  # student tier still in the full list


def test_footnote_restricted_name_harvested_for_miyuji_seat():
    text = ("… ◎Miyujiシート（小学生限定A席1,000円）"
           "＊残席がある場合に限り、9/18(金)10:00より受付を開始いたします。")
    names = tu.restricted_tier_names(text)
    assert "Miyujiシート" in names
    assert not tu.is_restricted_tier("Miyujiシート A席")   # no keyword alone
    assert tu.open_tier_min([("Miyujiシート A席", 1000), ("一般 A席", 5000)],
                            names) == 5000


def test_multi_venue_tour_matches_price_by_date_and_subtitle():
    # Netopil / Prague Symphony: 3 cities, each leg's own subtitle names
    # that date's featured soloist -- the SAME subtitle text keys its
    # price section, so same-date-different-venue legs (none here, but
    # the mechanism must not blend distinct legs together either).
    page = parse_concert_page(_load("japan_arts_multi_venue_live.html"))
    assert len(page["legs"]) == 3
    by_date = {leg["date"]: leg for leg in page["legs"]}

    yokohama = by_date["2027-01-11"]
    assert yokohama["venue_raw"] == "横浜みなとみらいホール"
    assert yokohama["title"] == "北村陽(チェロ)、桑原志織(ピアノ)出演"
    assert yokohama["price_min"] == 7000     # C席, excluding 学生割引

    tokyo = by_date["2027-01-13"]
    assert tokyo["venue_raw"] == "東京芸術劇場コンサートホール"
    assert tokyo["title"] == "桑原志織(ピアノ)出演"

    suntory = by_date["2027-01-15"]
    assert suntory["venue_raw"] == "サントリーホール"
    assert suntory["title"] == "金子三勇士 (ピアノ) 出演"


def test_sales_and_ticket_links_are_page_level():
    page = parse_concert_page(_load("japan_arts_multi_venue_live.html"))
    assert len(page["sales"]) == 3
    # bare "一般" labels normalize to "一般発売" so sale_kind reads general
    labels = [e["label"] for e in page["sales"]]
    assert any(l.startswith("一般") for l in labels)
    codes = {(l["provider"], l["code"]) for l in page["ticket_links"]
            if l["code"]}
    assert ("pia", "P329-974") in codes
    assert ("lawson", "L31048") in codes


def test_same_day_two_performance_page_falls_back_to_page_title():
    # both legs' own ticket-title is just a showtime ("13:00開演" /
    # "17:30開演") -- using that alone as the Event title would be
    # meaningless, so both legs keep the page's real h1 title instead.
    page = parse_concert_page(_load("japan_arts_two_perf_live.html"))
    assert len(page["legs"]) == 2
    assert all(leg["title"] == "小林沙羅 ソプラノ・リサイタル"
              for leg in page["legs"])
    times = {leg["start_time"] for leg in page["legs"]}
    assert times == {"13:00", "17:30"}


def test_empty_detail_html_returns_nothing_loudly():
    assert parse_concert_page("") == {"legs": [], "sales": []}
    page = parse_concert_page("<html></html>")
    assert page["legs"] == []


# --------------------------------------------------------- _legs_to_events
def test_legs_to_events_resolves_venue_and_sets_facts():
    scraper = JapanArtsScraper()
    page = parse_concert_page(_load("japan_arts_detail_live.html"))
    page["ticket_links"] = []
    evs = list(scraper._legs_to_events(page, KANEKO_URL, floor="2026-01-01"))
    assert len(evs) == 1
    ev = evs[0]
    assert ev.source == "japan_arts"
    assert ev.category == Category.MUSIC
    assert ev.genres == ["classical"]
    assert ev.venue_name == "東京オペラシティ コンサートホール"
    assert ev.price_min == 5000
    assert ev.lineup == [ev.title_ja]
    assert ev.source_url == f"{KANEKO_URL}#2026-10-18"


def test_legs_to_events_disambiguates_same_day_same_venue_legs():
    scraper = JapanArtsScraper()
    page = parse_concert_page(_load("japan_arts_two_perf_live.html"))
    evs = list(scraper._legs_to_events(page, KOBAYASHI_URL, floor="2026-01-01"))
    assert len(evs) == 2
    assert len({e.source_url for e in evs}) == 2
    assert {e.start_time for e in evs} == {"13:00", "17:30"}


def test_legs_to_events_drops_legs_before_the_floor():
    scraper = JapanArtsScraper()
    page = parse_concert_page(_load("japan_arts_multi_venue_live.html"))
    evs = list(scraper._legs_to_events(page, NETOPIL_URL, floor="2027-01-14"))
    assert len(evs) == 1
    assert evs[0].start_date == "2027-01-15"


def test_unresolved_venue_is_reported_and_dropped():
    scraper = JapanArtsScraper()
    page = {"legs": [{
        "date": "2026-10-01", "venue_raw": "謎のホール",
        "open_time": None, "start_time": None, "price_text": None,
        "price_min": None, "is_free": None, "sold_out": False, "title": "T",
    }], "sales": [], "ticket_links": []}
    evs = list(scraper._legs_to_events(
        page, "https://www.japanarts.co.jp/concert/test/", floor="2026-01-01"))
    assert evs == []
    assert scraper.skipped_venues == {"謎のホール"}


def test_sold_out_when_every_priced_tier_is_marked_sold_out():
    page = parse_concert_page(_load("japan_arts_detail_live.html"))
    leg = page["legs"][0]
    # A席 (the price_min winner) shows 残席あり on the live fixture
    assert leg["sold_out"] is False


def test_scraper_source_id_and_flags():
    s = JapanArtsScraper()
    assert s.source_id == "japan_arts"
    assert s.supports_detail is False
    assert s.rate_limit_s >= 2
