"""Admission-price detail pass (textutils.parse_admission + the museum
scrapers' parse_detail). The exported fact is the ADULT (一般) admission
— not the cheapest tier — or is_free for 入場無料 venues. Fixtures:
mori_ronmueck_detail_live.html + ggg_detail_live.html (captured
2026-07-26) plus detail pages already captured for earlier rings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.db import EventStore
from tokyo_events.models import Category, Event
from tokyo_events.scrapers import textutils as tu
from tokyo_events.scrapers.galleries import GggScraper, WhatMuseumScraper
from tokyo_events.scrapers.mori import MoriMuseumScraper
from tokyo_events.scrapers.museums import MitsuiScraper, YamataneScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _art_ev(source, url):
    return Event(source=source, source_url=url, title_ja="T",
                 category=Category.ART, start_date="2026-08-01",
                 end_date="2026-09-01")


# ------------------------------------------------------- parse_admission
def test_adult_price_wins_over_discount_and_child_tiers():
    # Yamatane shape: adult, presale in parens, child free — adult wins
    assert tu.parse_admission(
        "入館料 一般1400円(前売り1200円)、大学生・高校生1100円、"
        "中学生以下無料") == (1400, "一般 ¥1,400", None)
    # Mori shape: two tier blocks, first 一般 wins; 中学生以下 無料 must
    # NOT flip the show to free
    assert tu.parse_admission(
        "チケット・料金 一般 2,300円（2,100円） 学生 1,400円 "
        "中学生以下 無料 シニア 2,000円 一般 2,500円") == \
        (2300, "一般 ¥2,300", None)


def test_reversed_order_and_dayof_preference():
    # NACT prints "amount（tier）" and lists presale BEFORE day-of: the
    # day-of adult 2,400 must win — never the 1,200 university tier that
    # happens to follow the word 一般
    assert tu.parse_admission(
        "観覧料 前売 2,200円（一般）、1,200円（大学生）、800円（高校生） "
        "当日 2,400円（一般）、1,400円（大学生）、1,000円（高校生）") == \
        (2400, "一般 ¥2,400", None)
    # reversed order without a 当日 block
    assert tu.parse_admission("入場料 1,800円（一般） 900円（学生）") == \
        (1800, "一般 ¥1,800", None)


def test_bare_ryokin_label_needs_adult_marker():
    # a bare 料金 label followed by a premium/goods price without any 一般
    # marker must stay unknown, not become the admission
    assert tu.parse_admission(
        "料金 プレミアムチケット 6,500円（グッズ付き）") == (None, None, None)
    # but the strict admission labels may open with a bare price
    assert tu.parse_admission("入館料 1,000円") == (1000, "一般 ¥1,000", None)


def test_free_admission_and_label_free():
    assert tu.parse_admission("開館時間 11:00-19:00 入場無料") == \
        (None, None, True)
    assert tu.parse_admission("入館料 無料") == (None, None, True)


def test_admission_junk_and_fullwidth():
    assert tu.parse_admission("") == (None, None, None)
    assert tu.parse_admission("開館時間 10:00〜17:00") == (None, None, None)
    # merch-priced pages without an admission label stay unparsed
    assert tu.parse_admission("図録 3,000円で販売中") == (None, None, None)
    # fullwidth digits normalize
    assert tu.parse_admission("入館料 一般 １，５００円")[0] == 1500


def test_admission_sanity_bounds():
    assert tu.parse_admission("入館料 一般 50円") == (None, None, None)
    assert tu.parse_admission("料金 55,000円のプランも") == (None, None, None)


# ------------------------------------------------------- parse_detail
def test_mori_detail_lifts_adult_admission():
    ev = MoriMuseumScraper("mori_art_museum").parse_detail(
        _load("mori_ronmueck_detail_live.html"),
        _art_ev("mori_art_museum", "https://x/1"))
    assert ev.price_min == 2300              # weekday adult, not 土日祝 2,500
    assert ev.price_text == "一般 ¥2,300"
    assert ev.is_free is None


def test_ggg_detail_marks_free():
    ev = GggScraper().parse_detail(_load("ggg_detail_live.html"),
                                   _art_ev("ggg", "https://x/2"))
    assert ev.is_free is True
    assert ev.price_min is None


def test_what_and_mitsui_and_yamatane_details():
    ev = WhatMuseumScraper().parse_detail(
        _load("what_museum_detail_live.html"),
        _art_ev("what_museum", "https://x/3"))
    assert ev.price_min == 1500              # single-show adult, not the set

    ev = MitsuiScraper().parse_detail(
        _load("mitsui_index_live.html"), _art_ev("mitsui", "https://x/4"))
    assert ev.price_min == 1500

    ev = YamataneScraper().parse_detail(
        _load("yamatane_gyokudo_live.html"),
        _art_ev("yamatane", "https://x/5"))
    assert ev.price_min == 1400


def test_parse_detail_never_overwrites_known_price():
    ev = _art_ev("mitsui", "https://x/6")
    ev.price_min = 999
    out = MitsuiScraper().parse_detail(_load("mitsui_index_live.html"), ev)
    assert out.price_min == 999              # gap-fill only


# ------------------------------------------------------- backlog semantics
def test_free_art_event_leaves_detail_backlog(tmp_path):
    store = EventStore(tmp_path / "b.db")
    ev = _art_ev("ggg", "https://x/free")
    ev.start_date, ev.end_date = "2099-01-01", "2099-02-01"
    store.upsert(ev)
    assert len(store.events_needing_detail("ggg", set(), 10)) == 1
    ev.is_free = True                        # enriched: free admission
    store.upsert(ev)
    assert store.events_needing_detail("ggg", set(), 10) == []