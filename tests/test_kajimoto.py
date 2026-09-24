import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers import textutils as tu
from tokyo_events.scrapers.kajimoto import (KajimotoScraper, parse_concert_page,
                                            parse_schedule)

FIX = Path(__file__).parent / "fixtures"

LSO_URL = "https://www.kajimotomusic.com/concerts/lso-2026/"
NIIKURA_URL = "https://www.kajimotomusic.com/concerts/2026-hitomi-niikura-theroom/"
ARGERICH_URL = "https://www.kajimotomusic.com/concerts/2026-martha-argerich/"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------- listing
def test_listing_parses_the_whole_archive():
    # /concerts/schedule/ is one static page holding every show the
    # archive has ever listed (past and future) -- 357 as of 2026-09-24.
    rows = parse_schedule(_load("kajimoto_schedule_live.html"))
    assert len(rows) == 357
    urls = {r["detail_url"] for r in rows}
    assert LSO_URL in urls


def test_listing_row_fields():
    rows = parse_schedule(_load("kajimoto_schedule_live.html"))
    kei_itoh = next(r for r in rows if r["date"] == "2027-04-29")
    assert kei_itoh["title_en"] == "KEI ITOH ~THE LEGENDS~"
    assert kei_itoh["title_ja"] == "伊藤恵 ～THE LEGENDS～"
    assert kei_itoh["pref"] == "東京"
    assert kei_itoh["venue_raw"] == "日本製鉄紀尾井ホール"


def test_listing_filters_to_upcoming_by_floor_date():
    rows = parse_schedule(_load("kajimoto_schedule_live.html"))
    floor = "2026-09-24"
    upcoming = [r for r in rows if r["date"] >= floor]
    # exact count as of the 2026-09-24 fixture capture (16 Kanto+Kyoto legs)
    assert len(upcoming) == 16
    assert all(r["date"] >= floor for r in upcoming)


def test_empty_listing_html_returns_nothing_loudly():
    assert parse_schedule("") == []
    assert parse_schedule("<html></html>") == []


# ------------------------------------------------------------------ detail
def test_multi_leg_tour_page_parses_all_legs():
    # LSO Japan Tour 2026: Kyoto Concert Hall + 2x Suntory Hall, one page.
    page = parse_concert_page(_load("kajimoto_tour_live.html"))
    assert len(page["legs"]) == 3
    by_date = {leg["date"]: leg for leg in page["legs"]}

    kyoto = by_date["2026-09-26"]
    assert kyoto["pref"] == "京都"
    assert kyoto["venue_raw"] == "京都コンサートホール"
    assert kyoto["open_time"] == "15:15"
    assert kyoto["start_time"] == "16:00"
    assert kyoto["price_min"] == 11000       # D席 ¥11,000 is the floor
    assert kyoto["sold_out"] is False
    providers = {l["provider"] for l in kyoto["ticket_links"]}
    assert providers == {"eplus", "pia", "lawson"}

    tokyo1 = by_date["2026-09-28"]
    assert tokyo1["venue_raw"] == "サントリーホール"
    assert tokyo1["price_min"] == 17000
    tokyo2 = by_date["2026-09-29"]
    assert tokyo2["venue_raw"] == "サントリーホール"
    assert tokyo2["price_min"] == 18000


def test_kajimotos_own_eplus_storefront_is_a_ticket_link():
    # w1.onlineticket.jp ("カジモト・イープラス") doesn't contain the
    # literal "eplus.jp" substring but IS e+'s own white-label domain --
    # textutils.TICKET_PROVIDERS carries a dedicated entry for it.
    page = parse_concert_page(_load("kajimoto_tour_live.html"))
    kyoto = next(leg for leg in page["legs"] if leg["date"] == "2026-09-26")
    onlineticket_links = [l for l in kyoto["ticket_links"]
                          if "onlineticket.jp" in (l["url"] or "")]
    assert onlineticket_links and onlineticket_links[0]["provider"] == "eplus"


def test_sales_block_parses_presale_history_across_inline_tags():
    page = parse_concert_page(_load("kajimoto_tour_live.html"))
    labels = [e["label"] for e in page["sales"]]
    # "追加販売（<strong>関係者席開放</strong>）" is split across 3 text
    # nodes by the inline <strong> -- must reassemble into one label, not
    # get truncated to the last fragment ("）").
    assert "追加販売（関係者席開放）" in labels
    assert "一般発売" in labels

    from tokyo_events.scrapers.kajimoto import _event_sales
    sales = _event_sales(page["sales"], "2026-09-28")
    kinds = {e["label"]: e["kind"] for e in sales}
    assert kinds["一般発売"] == "general"
    assert kinds["追加販売（関係者席開放）"] == "presale"
    assert sales[0]["opens"] == "2026-05-17T12:00"      # earliest first


def test_student_tier_excluded_from_price_min():
    # "学生￥5,000 (＊学生券はカジモト・イープラスのみの取り扱い...)" must
    # never win price_min over the real S/A/B tiers.
    page = parse_concert_page(_load("kajimoto_two_venue_live.html"))
    for leg in page["legs"]:
        assert leg["price_min"] == 12000     # B席 ¥12,000, not 学生 ¥5,000
        assert "学生" in leg["price_text"]    # still kept in the full list


def test_lottery_presale_is_classified_correctly():
    page = parse_concert_page(_load("kajimoto_detail_live.html"))
    from tokyo_events.scrapers.kajimoto import _event_sales
    sales = _event_sales(page["sales"], "2026-12-03")
    kinds = {e["label"]: e["kind"] for e in sales}
    assert kinds["新倉瞳Official Members “瞳の小部屋” メンバーズ先行（抽選）"] \
        == "lottery"
    assert kinds["一般発売"] == "general"


def test_page_with_no_sales_block_yields_empty_list():
    # martha-argerich's own page prints no "TICKETS" WordPress block.
    page = parse_concert_page(_load("kajimoto_two_venue_live.html"))
    assert page["sales"] == []


def test_empty_detail_html_returns_nothing_loudly():
    assert parse_concert_page("") == {"legs": [], "sales": []}
    assert parse_concert_page("<html></html>") == {"legs": [], "sales": []}


# --------------------------------------------------------- _legs_to_events
def test_legs_to_events_resolves_venues_and_sets_facts():
    scraper = KajimotoScraper()
    page = parse_concert_page(_load("kajimoto_tour_live.html"))
    listing_rows = [{
        "title_ja": "サー・アントニオ・パッパーノ指揮 ロンドン交響楽団 JAPAN TOUR 2026",
        "title_en": "LONDON SYMPHONY ORCHESTRA JAPAN TOUR 2026",
    }]
    evs = list(scraper._legs_to_events(page, LSO_URL, listing_rows,
                                       floor="2026-01-01"))
    # Kyoto (京都コンサートホール) is out of Kanto scope and stays
    # unresolved by design -- only the 2 Tokyo/Suntory legs survive.
    assert len(evs) == 2
    assert all(e.source == "kajimoto" for e in evs)
    assert all(e.category == Category.MUSIC for e in evs)
    assert all(e.genres == ["classical"] for e in evs)
    assert all(e.title_ja == listing_rows[0]["title_ja"] for e in evs)
    assert all(e.lineup == [listing_rows[0]["title_ja"]] for e in evs)
    assert scraper.skipped_venues == set()   # 京都 isn't a Kanto pref hint
    # same-date legs never collide
    urls = {e.source_url for e in evs}
    assert len(urls) == 2
    tokyo_28 = next(e for e in evs if e.start_date == "2026-09-28")
    assert tokyo_28.source_url == f"{LSO_URL}#2026-09-28"
    assert tokyo_28.venue_name == "サントリーホール"


def test_legs_to_events_drops_legs_before_the_floor():
    scraper = KajimotoScraper()
    page = parse_concert_page(_load("kajimoto_tour_live.html"))
    listing_rows = [{"title_ja": "T", "title_en": "T"}]
    evs = list(scraper._legs_to_events(page, LSO_URL, listing_rows,
                                       floor="2026-09-29"))
    assert len(evs) == 1
    assert evs[0].start_date == "2026-09-29"


def test_unresolved_kanto_venue_is_reported_and_dropped():
    scraper = KajimotoScraper()
    page = {"legs": [{
        "date": "2026-10-01", "pref": "東京", "venue_raw": "謎の東京ホール",
        "open_time": None, "start_time": None, "price_text": None,
        "price_min": None, "is_free": None, "ticket_links": [],
        "sold_out": False,
    }], "sales": []}
    evs = list(scraper._legs_to_events(
        page, "https://www.kajimotomusic.com/concerts/test/",
        [{"title_ja": "T", "title_en": "T"}], floor="2026-01-01"))
    assert evs == []
    assert scraper.skipped_venues == {"謎の東京ホール"}


def test_non_kanto_unresolved_venue_is_not_reported():
    # a leg with no Kanto prefecture hint (and not the blank-prefecture
    # case either) must not pollute the skipped_venues report.
    scraper = KajimotoScraper()
    page = {"legs": [{
        "date": "2026-10-01", "pref": "大阪", "venue_raw": "謎の大阪ホール",
        "open_time": None, "start_time": None, "price_text": None,
        "price_min": None, "is_free": None, "ticket_links": [],
        "sold_out": False,
    }], "sales": []}
    evs = list(scraper._legs_to_events(
        page, "https://www.kajimotomusic.com/concerts/test/",
        [{"title_ja": "T", "title_en": "T"}], floor="2026-01-01"))
    assert evs == []
    assert scraper.skipped_venues == set()


def test_same_day_same_venue_legs_get_distinct_source_urls():
    # a hypothetical matinee+evening at the same hall must not collide on
    # one "#date-venue" fragment -- start_time disambiguates.
    scraper = KajimotoScraper()
    page = {"legs": [
        {"date": "2026-10-01", "pref": "東京", "venue_raw": "サントリーホール",
         "open_time": "12:30", "start_time": "13:00", "price_text": None,
         "price_min": 5000, "is_free": False, "ticket_links": [],
         "sold_out": False},
        {"date": "2026-10-01", "pref": "東京", "venue_raw": "サントリーホール",
         "open_time": "17:00", "start_time": "17:30", "price_text": None,
         "price_min": 5000, "is_free": False, "ticket_links": [],
         "sold_out": False},
    ], "sales": []}
    evs = list(scraper._legs_to_events(
        page, "https://www.kajimotomusic.com/concerts/test/",
        [{"title_ja": "T", "title_en": "T"}], floor="2026-01-01"))
    assert len(evs) == 2
    assert len({e.source_url for e in evs}) == 2


def test_sold_out_leg_is_flagged():
    page = {"legs": [{
        "date": "2026-10-01", "pref": "東京", "venue_raw": "サントリーホール",
        "open_time": None, "start_time": None, "price_text": None,
        "price_min": None, "is_free": None, "ticket_links": [],
        "sold_out": True,
    }], "sales": []}
    scraper = KajimotoScraper()
    evs = list(scraper._legs_to_events(
        page, "https://www.kajimotomusic.com/concerts/test/",
        [{"title_ja": "T", "title_en": "T"}], floor="2026-01-01"))
    assert evs[0].is_sold_out is True


def test_scraper_source_id_and_flags():
    s = KajimotoScraper()
    assert s.source_id == "kajimoto"
    assert s.supports_detail is False
    assert s.rate_limit_s >= 2


def test_restricted_tier_helper_matches_kajimotos_student_tier():
    assert tu.is_restricted_tier(
        "学生￥5,000 (＊学生券はカジモト・イープラスのみの取り扱い"
        "／B席相当・当日引換)")
    assert not tu.is_restricted_tier("S席 ¥39,000")
