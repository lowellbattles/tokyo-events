import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.db import EventStore
from tokyo_events.models import Event, ReviewStatus
from tokyo_events.scrapers.liquidroom import LiquidroomScraper
from tokyo_events.scrapers.zepp import ZeppScraper
from tokyo_events.scrapers.ogroup import OGroupScraper
from tokyo_events.scrapers.billboard import BillboardScraper
from tokyo_events.scrapers import textutils as tu

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 7, 2)   # pin 'today' so year inference is deterministic


def _load(name):
    return (FIX / name).read_text()


# --------------------------------------------------------------- liquidroom
def _lq():
    return {e.source_url.split("/")[-1]: e
            for e in LiquidroomScraper().parse(_load("liquidroom_schedule.html"))}


def test_liquidroom_parses_all_and_dedupes_teasers():
    assert len(_lq()) == 5


def test_liquidroom_fields():
    e = _lq()["betaband_20260609"]
    assert (e.title_ja, e.subtitle) == ("THE BETA BAND", "JAPAN TOUR 2026")
    assert (e.start_date, e.open_time, e.start_time) == \
        ("2026-06-09", "18:00", "19:00")
    assert e.price_min == 8800 and e.venue_name == "LIQUIDROOM"


def test_liquidroom_combined_open_start_and_sold_out():
    ev = _lq()
    assert ev["takbamjapantour_20260612"].open_time == "23:00"
    assert ev["takbamjapantour_20260612"].start_time == "23:00"
    assert ev["hagane_20260623"].is_sold_out
    assert ev["matenrouopera_20260607-2"].start_date == "2026-06-07"


# --------------------------------------------------------------------- zepp
def _zepp():
    return {e.source_url.split("rid=")[-1]: e
            for e in ZeppScraper("zepp_divercity").parse(
                _load("zepp_schedule.html"), today=TODAY)}


def test_zepp_parses_all_events():
    assert len(_zepp()) == 3


def test_zepp_fields_and_relative_url_join():
    e = _zepp()["155496"]
    assert e.title_ja == "East Of Eden"
    assert e.start_date == "2026-07-03"          # year inferred from 7.3(金)
    assert (e.open_time, e.start_time) == ("18:00", "19:00")
    assert e.price_min == 7500                   # min of tier prices
    assert e.source_url.startswith("https://www.zepp.co.jp/")
    assert e.venue_name == "Zepp DiverCity (TOKYO)"


def test_zepp_sold_out():
    assert _zepp()["158968"].is_sold_out


def test_zepp_hall_parameterization():
    s = ZeppScraper("zepp_haneda")
    assert s.source_id == "zepp_haneda"
    assert s.hall["venue_name"] == "Zepp Haneda (TOKYO)"


# ------------------------------------------------------------------ o-group
def _oeast():
    return {e.source_url.rstrip("/").split("/")[-1]: e
            for e in OGroupScraper("oeast").parse(
                _load("ogroup_schedule.html"), today=TODAY)}


def test_ogroup_parses_own_hall_only():
    evs = _oeast()
    assert len(evs) == 3
    assert "other-hall-event" not in evs   # west link ignored by east scraper


def test_ogroup_fields_and_year_inference():
    evs = _oeast()
    e = evs["snowdrop-japan-tour-2026"]
    assert e.start_date == "2026-07-03" and e.price_min == 8500
    assert (e.open_time, e.start_time) == ("17:00", "18:00")
    # "6.26 fri" with no year -> inferred 2026 (recent past OK within window)
    assert evs["pizza-of-death-hub-slice7"].start_date == "2026-06-26"
    assert evs["pizza-of-death-hub-slice7"].open_time == "24:00"


# ---------------------------------------------------------------- billboard
def _bb():
    return BillboardScraper("billboard_tokyo").parse(
        _load("billboard_schedule.html"))


def test_billboard_one_event_per_night_ignores_nonshow_links():
    evs = _bb()
    assert len(evs) == 3   # 2 Patrice nights + Carnation; rental/free skipped
    dates = sorted(e.start_date for e in evs)
    assert dates == ["2026-06-01", "2026-06-02", "2026-06-13"]


def test_billboard_fields():
    e = next(e for e in _bb() if e.start_date == "2026-06-01")
    assert e.title_ja == "Patrice Rushen"
    assert (e.open_time, e.start_time) == ("16:30", "17:30")   # 1st stage
    assert e.price_min == 12400                                # casual tier
    assert "2-stages" in e.tags
    assert e.genres == ["jazz-soul"]
    assert e.venue_name == "Billboard Live TOKYO"


# --------------------------------------------------------- detail enrichment
def test_detail_enrichment_fills_gaps_and_ticket_links():
    ev = Event(source="liquidroom",
               source_url="https://www.liquidroom.net/schedule/androp_20260703",
               title_ja="androp", start_date="2026-07-03")
    enriched = LiquidroomScraper().parse_detail(_load("detail_page.html"), ev)
    assert (enriched.open_time, enriched.start_time) == ("18:00", "19:00")
    assert enriched.price_min == 5500          # goods ¥3,000 not picked up
    providers = {t["provider"] for t in enriched.ticket_links}
    assert {"eplus", "pia", "lawson"} <= providers
    codes = {t["code"] for t in enriched.ticket_links if t["code"]}
    assert "P299-456" in codes and "L71234" in codes


def test_detail_enrichment_never_overwrites_listing_data():
    ev = Event(source="liquidroom", source_url="x", title_ja="androp",
               open_time="17:30", start_time="18:30", price_min=6000,
               price_text="¥6,000")
    enriched = LiquidroomScraper().parse_detail(_load("detail_page.html"), ev)
    assert (enriched.open_time, enriched.price_min) == ("17:30", 6000)


# ------------------------------------------------------------------- utils
def test_infer_year_rolls_forward():
    # Jan 15 seen in July 2026 -> must be Jan 2027, not 6 months ago
    assert tu.infer_year(1, 15, dt.date(2026, 7, 2)) == "2027-01-15"
    assert tu.infer_year(7, 3, dt.date(2026, 7, 2)) == "2026-07-03"


# ------------------------------------------------------------------- store
def test_store_staging_workflow(tmp_path):
    store = EventStore(tmp_path / "test.db")
    ev = _lq()["betaband_20260609"]

    assert store.upsert(ev) == "new"
    assert store.upsert(ev) == "unchanged"
    assert store.list_events(public_only=True) == []

    store.set_status(ev.dedupe_key(), ReviewStatus.APPROVED)
    assert len(store.list_events(public_only=True)) == 1

    ev.price_text = "¥9,000"
    assert store.upsert(ev) == "changed"       # re-staged for review
    assert store.list_events(public_only=True) == []


# ------------------------------------------------------------------- pia
from tokyo_events.scrapers.pia import ToyosuPitScraper, PiaArenaMMScraper


def test_toyosu_parses_events_and_infers_year():
    evs = {e.source_url.split("/")[-1]: e
           for e in ToyosuPitScraper().parse(
               _load("toyosu_schedule.html"), today=TODAY)}
    assert len(evs) == 3
    e = evs["8554.html"]
    assert e.title_ja == 'CHEHON ONEMAN LIVE 2026 "KING OF STAGE"'
    assert e.lineup == ["CHEHON"]           # artist heading -> lineup
    assert e.start_date == "2026-07-03"     # URL says 202606; text wins
    assert evs["8441.html"].source_url.startswith("https://toyosu.pia-pit.jp/")
    assert evs["8441.html"].open_time == "17:00"
    assert evs["8518.html"].start_date == "2026-11-09"


def test_pia_arena_skips_private_days():
    evs = PiaArenaMMScraper().parse(_load("pia_arena_schedule.html"),
                                    month=dt.date(2026, 7, 1))
    assert len(evs) == 4                     # PRIVATE 07.02 excluded
    assert not any("PRIVATE" in (e.title_ja or "") for e in evs)


def test_pia_arena_fields():
    evs = {e.source_url.split("/")[-1]: e
           for e in PiaArenaMMScraper().parse(
               _load("pia_arena_schedule.html"), month=dt.date(2026, 7, 1))}
    assert evs["6924.html"].title_ja == "日向坂46 五期生 LIVE"
    assert evs["6924.html"].start_date == "2026-07-15"
    assert evs["6924.html"].venue_name == "ぴあアリーナMM"
    # doubled title collapses: "MyGO!!!!! MyGO!!!!! 9th LIVE"
    assert evs["6217.html"].title_ja == "MyGO!!!!!"
    assert "9th LIVE" in (evs["6217.html"].subtitle or "")


# ------------------------------------------- live captures (2026-07-12)
# Raw HTML saved from the real sites on first live validation. These pin
# the formats that broke the rendered-capture parsers: O-Group "07 / 01
# WED" spaced dates, Toyosu/Pia relative hrefs, Toyosu artist-in-heading.
LIVE_TODAY = dt.date(2026, 7, 12)


def test_ogroup_live_page_parses():
    evs = {e.source_url.rstrip("/").split("/")[-1]: e
           for e in OGroupScraper("oeast").parse(
               _load("ogroup_east_live.html"), today=LIVE_TODAY)}
    assert len(evs) == 35
    e = evs["snowdrop-japan-tour-2026"]
    assert "Snowdrop" in e.title_ja
    assert e.start_date == "2026-07-03"     # "07 / 03 FRI" spaced format
    assert (e.open_time, e.start_time) == ("18:00", "19:00")
    assert evs["never-ending-homies"].is_sold_out


def test_toyosu_live_list_parses():
    evs = {e.source_url.split("/")[-1]: e
           for e in ToyosuPitScraper().parse(
               _load("toyosu_list_live.html"), today=LIVE_TODAY)}
    assert len(evs) == 12
    e = evs["8554.html"]
    # "../schedule/202606/8554.html" relative href joined correctly
    assert e.source_url == "https://toyosu.pia-pit.jp/schedule/202606/8554.html"
    assert e.title_ja == 'CHEHON ONEMAN LIVE 2026 "KING OF STAGE"'
    assert e.lineup == ["CHEHON"]
    assert e.start_date == "2026-07-03"


def test_pia_arena_live_month_parses():
    evs = {e.source_url.split("/")[-1]: e
           for e in PiaArenaMMScraper().parse(
               _load("pia_arena_month_live.html"), month=dt.date(2026, 7, 1))}
    assert len(evs) == 12                   # PRIVATE hall-rental days skipped
    assert not any("PRIVATE" in (e.title_ja or "") for e in evs.values())
    # "event/6924.html" relative href joined correctly
    e = evs["6924.html"]
    assert e.source_url == "https://pia-arena-mm.jp/event/6924.html"
    assert e.title_ja == "日向坂46 五期生 LIVE"
    assert e.start_date == "2026-07-15"
