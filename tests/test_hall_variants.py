"""Own-hall fixture coverage for the O-Group / Zepp / Billboard hall
families (roadmap R16 / TST-2 in docs/architecture-and-roadmap.md).

Seven registered source_ids ride hall-parameterized scraper modules but,
before this file, had no fixture captured from their OWN hall's page:
owest, ocrest, onest (scrapers/ogroup.py), zepp_shinjuku, zepp_yokohama,
zepp_haneda (scrapers/zepp.py), billboard_yokohama (scrapers/billboard.py).
Existing fixtures only proved the shared parse code once per module (via
oeast, zepp_divercity, billboard_tokyo) -- never the per-hall config
(slug, venue_name/area, href-scoping regex) wired up for these seven.

Fixtures were captured live 2026-08-03 (one listing page per hall, politely
-- see the capture script's own request log in the PR/session notes) and
saved verbatim (scrubbed for secrets; none were found) as
tests/fixtures/<source_id>_live.html (zepp: *_month_live.html, following
the existing zepp_schedule_month_live.html naming). TODAY is pinned to the
capture date so O-Group's no-year "MM / DD DAYNAME" listing format
resolves through the same infer_year() window every run, forever --
independent of the real wall clock.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.scrapers.ogroup import OGroupScraper
from tokyo_events.scrapers.zepp import ZeppScraper
from tokyo_events.scrapers.billboard import BillboardScraper

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 8, 3)          # pin 'today' to the live-capture date
AUG_START, AUG_END = dt.date(2026, 8, 1), dt.date(2026, 8, 31)


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _assert_iso_dates_in_captured_month(evs):
    """Every event's start_date parses as ISO (fromisoformat raises
    otherwise) and falls inside the August-2026 page that was captured."""
    for e in evs:
        d = dt.date.fromisoformat(e.start_date)
        assert AUG_START <= d <= AUG_END, (e.source_url, e.start_date)


# ------------------------------------------------------------- O-Group ----
def _ogroup(hall_id, fname):
    return {e.source_url.rstrip("/").split("/")[-1]: e
            for e in OGroupScraper(hall_id).parse(_load(fname), today=TODAY)}


def test_owest_own_hall_fixture():
    scraper = OGroupScraper("owest")
    assert scraper.source_id == "owest"
    evs = _ogroup("owest", "owest_live.html")
    assert len(evs) == 31

    first = evs["one-night-summer-live-2026"]
    assert first.title_ja == "One Night Summer Live 2026 〜色祭〜"
    assert first.start_date == "2026-08-01"

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    # O-Group's HALLS config carries no per-hall venue_area (unlike
    # zepp/billboard) -- _parse_block hardcodes "Shibuya" directly, so
    # that is the real configured value to pin here.
    assert all(e.venue_area == "Shibuya" for e in evs.values())
    _assert_iso_dates_in_captured_month(evs.values())

    # O-WEST's listing blocks carry no ADV/前売 price marker at all (each
    # block is just date + OPEN/START + title -- confirmed against the raw
    # anchor text), unlike oeast's fixture. A real per-hall difference on
    # the same WordPress platform, not a parser miss.
    assert all(e.price_min is None for e in evs.values())

    assert sum(1 for e in evs.values() if e.is_sold_out) == 3
    assert evs["jyocho-one-man-show-2026-%e3%80%8c%e5%bf%98%e3%82%8c%e3%81%9f"
                "%e3%81%8f%e3%81%aa%e3%81%84%e3%81%93%e3%81%a8%e3%80%8d"
                ].title_ja == "JYOCHO one man show 2026 「忘れたくないこと」"


def test_ocrest_own_hall_fixture():
    scraper = OGroupScraper("ocrest")
    assert scraper.source_id == "ocrest"
    evs = _ogroup("ocrest", "ocrest_live.html")
    assert len(evs) == 33

    first = evs["manaco-live-tour-2026-bluish"]
    assert first.title_ja == 'manaco Live Tour 2026 "Bluish"'
    assert first.start_date == "2026-08-01"

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    assert all(e.venue_area == "Shibuya" for e in evs.values())   # hardcoded, see owest test
    _assert_iso_dates_in_captured_month(evs.values())

    # Same platform, same story as owest: no price markers in the listing.
    assert all(e.price_min is None for e in evs.values())

    # Pinned real fact: nothing on O-Crest's page is currently sold out.
    assert sum(1 for e in evs.values() if e.is_sold_out) == 0


def test_onest_own_hall_fixture():
    scraper = OGroupScraper("onest")
    assert scraper.source_id == "onest"
    evs = _ogroup("onest", "onest_live.html")
    assert len(evs) == 33

    first = evs["sugar%e2%99%a1holic-%e5%a4%a7%e7%9f%b3-%e8%8a%b1%e9%9f%b3"
                "%e5%8d%92%e6%a5%ad%e5%85%ac%e6%bc%94"]
    assert first.title_ja == "Sugar♡Holic 大石 花音卒業公演"
    assert first.start_date == "2026-08-01"

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    assert all(e.venue_area == "Shibuya" for e in evs.values())   # hardcoded, see owest test
    _assert_iso_dates_in_captured_month(evs.values())

    assert all(e.price_min is None for e in evs.values())

    assert sum(1 for e in evs.values() if e.is_sold_out) == 2
    assert evs["suiseinoboaz-one-man-show-2026-summer"].title_ja == \
        "SuiseiNoboAz ONE MAN SHOW 2026 SUMMER"


# ---------------------------------------------------------------- Zepp ----
def _zepp(hall_id, fname):
    return {e.source_url.split("rid=")[-1]: e
            for e in ZeppScraper(hall_id).parse(_load(fname), today=TODAY)}


def test_zepp_shinjuku_own_hall_fixture():
    scraper = ZeppScraper("zepp_shinjuku")
    assert scraper.source_id == "zepp_shinjuku"
    evs = _zepp("zepp_shinjuku", "zepp_shinjuku_month_live.html")
    assert len(evs) == 26

    first = evs["165088"]
    assert first.title_ja == "末吉9太郎"
    assert first.start_date == "2026-08-01"
    assert first.price_min == 5000

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    assert all(e.venue_area == scraper.hall["venue_area"] for e in evs.values())
    _assert_iso_dates_in_captured_month(evs.values())

    prices = [e.price_min for e in evs.values() if e.price_min is not None]
    assert len(prices) == 26                     # every event carries [PRICE]
    assert all(isinstance(p, int) for p in prices)
    # Real observed floor is below the usual 1,000 sanity line: rid=159532
    # ("電脳ヒメカ") lists a genuine ¥500 "one-coin" trial tier alongside
    # ¥8,000/¥3,000/¥1,000 tiers -- parse_prices() correctly takes the min.
    assert min(prices) == 500 and max(prices) == 9900

    assert sum(1 for e in evs.values() if e.is_sold_out) == 2
    # Same event: its OWN tour subtitle contains the literal word
    # '"SOLDOUT"' (referencing a past leg that sold out, prompting this
    # "再挑戦" retry date) -- SOLD_OUT_RE correctly matches that text, even
    # though it doesn't necessarily mean *this* 8/4 date is sold out. A
    # real vocabulary edge case in the source page, not a fixture bug.
    assert evs["159532"].is_sold_out is True


def test_zepp_yokohama_own_hall_fixture():
    scraper = ZeppScraper("zepp_yokohama")
    assert scraper.source_id == "zepp_yokohama"
    evs = _zepp("zepp_yokohama", "zepp_yokohama_month_live.html")
    assert len(evs) == 27

    first = evs["158672"]
    assert first.title_ja == (
        "beatnation [dj TAKA / DJ YOSHITAKA / Sota Fujimori/ L.E.D. / "
        "kors k / 猫叉Master / Ryu☆] VENUS / Hommarju / RoughSketch / "
        "かめりあTORIENA / めめめ / KE!JU / TAN1CHU pop'n musicMini Live "
        "D-crew / wac / PON / 劇団レコード / NU-KO / mami秋成 / red "
        "glasses / good-cool / すわひでお Dancer :LEO / ViolaVJ :Motion "
        "CombatまぐかべLaser :CRYSTALINOCostume Provider :CuLLt")
    assert first.start_date == "2026-08-01"
    assert first.price_min == 8800

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    assert all(e.venue_area == scraper.hall["venue_area"] for e in evs.values())
    _assert_iso_dates_in_captured_month(evs.values())

    prices = [e.price_min for e in evs.values() if e.price_min is not None]
    assert len(prices) == 27
    assert all(isinstance(p, int) for p in prices)
    assert min(prices) == 1000 and max(prices) == 14800   # sits inside 1k-30k

    assert sum(1 for e in evs.values() if e.is_sold_out) == 0


def test_zepp_haneda_own_hall_fixture():
    scraper = ZeppScraper("zepp_haneda")
    assert scraper.source_id == "zepp_haneda"
    evs = _zepp("zepp_haneda", "zepp_haneda_month_live.html")
    assert len(evs) == 25

    first = evs["157223"]
    assert first.title_ja == "ファントムシータ"
    assert first.start_date == "2026-08-01"
    assert first.price_min == 7000

    assert all(e.venue_name == scraper.hall["venue_name"] for e in evs.values())
    assert all(e.venue_area == scraper.hall["venue_area"] for e in evs.values())
    _assert_iso_dates_in_captured_month(evs.values())

    prices = [e.price_min for e in evs.values() if e.price_min is not None]
    assert len(prices) == 25
    assert all(isinstance(p, int) for p in prices)
    # Two genuine sub-1,000 outliers, both confirmed against the raw block
    # text (not fixture damage):
    #  - rid=164690 is really ¥0: a Suntory-sponsored non-alcohol idol
    #    party, general admission, "全自由(整理番号)/ ¥0".
    #  - rid=159881 parses to ¥30 (not a real ticket price -- the page's
    #    own markup splits "¥30,000" so YEN_RE only captures the leading
    #    "30"; the true minimum tier is actually the plain ¥9,800 walk-up
    #    ticket). A real site-side markup quirk our min-of-tiers heuristic
    #    can't see through; documented here rather than silently ignored.
    assert min(prices) == 0 and max(prices) == 13800
    assert evs["164690"].price_min == 0
    assert evs["159881"].price_min == 30

    assert sum(1 for e in evs.values() if e.is_sold_out) == 0


# ----------------------------------------------------------- Billboard ----
def test_billboard_yokohama_own_hall_fixture():
    scraper = BillboardScraper("billboard_yokohama")
    assert scraper.source_id == "billboard_yokohama"
    evs = BillboardScraper("billboard_yokohama").parse(
        _load("billboard_yokohama_live.html"))
    assert len(evs) == 19

    first = evs[0]
    assert first.source_url.split("event_id=")[-1].split("&")[0] == "ev-21403"
    assert first.title_ja == "石川ひとみ 「まちぶせ」発売45周年記念Live with Strings"
    assert first.start_date == "2026-08-02"
    assert first.price_min == 8800

    assert all(e.venue_name == scraper.club["venue_name"] for e in evs)
    assert all(e.venue_area == scraper.club["venue_area"] for e in evs)
    _assert_iso_dates_in_captured_month(evs)

    prices = [e.price_min for e in evs if e.price_min is not None]
    assert len(prices) == 19                      # every night carries a price
    assert all(isinstance(p, int) for p in prices)
    assert min(prices) == 5200 and max(prices) == 11500   # comfortably 1k-30k

    assert sum(1 for e in evs if e.is_sold_out) == 0
    assert all(e.genres == ["jazz-soul"] for e in evs)     # Billboard prior
