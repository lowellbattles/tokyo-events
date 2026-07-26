"""Mori family (mori_art_museum / mori_arts_center_gallery) — the ART
phase's first sources. Fixtures captured 2026-07-26 (UTF-8, scrub-checked):
/jp/exhibitions/ + /en/exhibitions/ for both sites (same CMS template).

On the fixtures the dated own-site exhibitions are:
- MAM:  ronmueck 2026-04-29..09-23, marikomori 2026-10-31..2027-03-28
- MACG: fate-go 2026-07-17..09-14, haikyu-challengers 2026-10-30..2027-01-11
Undated satellite programs (MAM Collection/Screen/Research), the shop tile
and cross-promos to sibling facilities (TCV, MACG-on-MAM, membership) must
all be excluded.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers import mori as M
from tokyo_events.scrapers.mori import MoriMuseumScraper, parse_date_range
from tokyo_events.venues import resolve_venue, vclass_of

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _mam():
    return {e.source_url: e for e in MoriMuseumScraper("mori_art_museum").parse(
        _load("mori_art_museum_jp_live.html"),
        en_html=_load("mori_art_museum_en_live.html"))}


def _macg():
    return {e.source_url: e
            for e in MoriMuseumScraper("mori_arts_center_gallery").parse(
                _load("mori_arts_center_gallery_jp_live.html"),
                en_html=_load("mori_arts_center_gallery_en_live.html"))}


# --------------------------------------------------------------- date ranges
def test_date_range_same_year_jp_and_en():
    assert parse_date_range("2026.4.29（水）〜 9.23（水）") == \
        ("2026-04-29", "2026-09-23")
    assert parse_date_range("2026.4.29 [Wed] - 9.23 [Wed]") == \
        ("2026-04-29", "2026-09-23")


def test_date_range_cross_year_explicit_and_inferred():
    assert parse_date_range("2026.10.31（土）〜 2027.3.28（日）") == \
        ("2026-10-31", "2027-03-28")
    # end year omitted but before the start month -> wrapped a year boundary
    assert parse_date_range("2026.12.20（日）〜 1.11（月）") == \
        ("2026-12-20", "2027-01-11")


def test_date_range_rejects_junk():
    assert parse_date_range("") == (None, None)
    assert parse_date_range("会期未定") == (None, None)
    assert parse_date_range("9.23（水）〜 2026.4.29") == (None, None)  # no start year
    assert parse_date_range("2026.4.29（水）") == (None, None)         # no end


# ------------------------------------------------------------ mori_art_museum
def test_mam_two_dated_exhibitions_bilingual():
    evs = _mam()
    assert len(evs) == 2
    ron = evs["https://www.mori.art.museum/jp/exhibitions/ronmueck/"]
    assert ron.title_ja == "ロン・ミュエク"
    assert ron.title_en == "Ron Mueck"
    assert (ron.start_date, ron.end_date) == ("2026-04-29", "2026-09-23")
    assert ron.category is Category.ART
    assert ron.venue_name == "森美術館"

    mariko = evs["https://www.mori.art.museum/jp/exhibitions/marikomori/"]
    assert mariko.title_en == "Mariko Mori: All That Shines"
    # cross-year run printed with an explicit end year on the listing
    assert (mariko.start_date, mariko.end_date) == ("2026-10-31", "2027-03-28")


def test_mam_excludes_satellites_shop_and_cross_promos():
    urls = " ".join(_mam())
    # dateless satellite programs are not events (facts require dates)
    for slug in ("mamcollection", "mamscreen", "mamresearch", "moricaf"):
        assert slug not in urls
    # absolute-URL tiles: online shop, Tokyo City View, MACG cross-promo
    assert "shop.mori.art.museum" not in urls
    assert "tcv.roppongihills" not in urls
    assert "macg.roppongihills" not in urls


# ------------------------------------------------- mori_arts_center_gallery
def test_macg_two_exhibitions_incl_inferred_cross_year():
    evs = _macg()
    assert len(evs) == 2
    fate = evs["https://macg.roppongihills.com/jp/exhibitions/fate-go/"]
    assert fate.title_ja == "Fate/Grand Order展 -星見の回廊-"
    assert fate.title_en == "Fate/Grand Order exhibition -stargazer-"
    assert (fate.start_date, fate.end_date) == ("2026-07-17", "2026-09-14")

    haikyu = evs["https://macg.roppongihills.com/jp/exhibitions/"
                 "haikyu-challengers/"]
    assert (haikyu.start_date, haikyu.end_date) == ("2026-10-30", "2027-01-11")
    assert haikyu.venue_name == "森アーツセンターギャラリー"
    # MAM + TCV cross-promos on the MACG page are absolute URLs -> excluded
    assert not any("mori.art.museum" in u for u in evs)


# ------------------------------------------------------------------ contract
def test_en_mirror_failure_still_yields_jp_events():
    evs = MoriMuseumScraper("mori_art_museum").parse(
        _load("mori_art_museum_jp_live.html"), en_html=None)
    assert len(evs) == 2
    assert all(e.title_ja and e.title_en is None for e in evs)


def test_structural_failure_is_loud():
    assert MoriMuseumScraper("mori_art_museum").parse("<html></html>") == []


def test_venue_registry_and_flags():
    for sid in ("mori_art_museum", "mori_arts_center_gallery"):
        s = MoriMuseumScraper(sid)
        assert resolve_venue(s.venue["venue_name"]) == sid
        assert vclass_of(sid) == "museum"
        assert s.supports_detail is False
        assert s.rate_limit_s >= 2.0
    assert M._SITES.keys() == {"mori_art_museum", "mori_arts_center_gallery"}
