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
    return (FIX / name).read_text(encoding="utf-8")


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


# ------------------------------------------------------------------ genres
from tokyo_events.genres import rule_genres


def test_rule_genres_keywords_and_defaults():
    idol, conf = rule_genres({"source": "zepp_haneda",
                              "title_ja": "AKB48 19期生コンサート"})
    assert idol == ["idol"] and conf
    intl, conf = rule_genres({"source": "toyosu_pit",
                              "title_ja": "FLO Therapy At The Club Tour 2026 JAPAN."})
    assert "international" in intl and conf
    kp, _ = rule_genres({"source": "pia_arena_mm", "title_ja": "김재중 ASIA TOUR"})
    assert "k-pop" in kp
    anime, _ = rule_genres({"source": "pia_arena_mm",
                            "title_ja": "ラブライブ！サンシャイン!! LIVE"})
    assert anime[0] == "anime-seiyu"
    # live-house default: unrecognized band -> j-rock, NOT confident
    dflt, conf = rule_genres({"source": "zepp_divercity", "title_ja": "TRACK15"})
    assert dflt == ["j-rock"] and not conf
    # arenas get no default
    none, conf = rule_genres({"source": "pia_arena_mm", "title_ja": "謎の公演"})
    assert none == [] and not conf


def test_export_applies_genres(tmp_path):
    from tokyo_events.db import EventStore
    import json as _json
    store = EventStore(tmp_path / "g.db")
    ev = _lq()["betaband_20260609"]         # subtitle "JAPAN TOUR 2026"
    store.upsert(ev, ReviewStatus.AUTO)
    out = tmp_path / "pub.json"
    store.export_public_json(out)
    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["events"][0]["genres"] == ["international"]


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


# ------------------------------------- roadmap step-3 families (live 2026-07-12)
from tokyo_events.scrapers.quattro import QuattroScraper
from tokyo_events.scrapers.www import WWWScraper
from tokyo_events.scrapers.duo import DuoScraper
from tokyo_events.scrapers.loft import LoftScraper

JUL = dt.date(2026, 7, 1)


def test_quattro_parses_month_and_keeps_query_ids():
    evs = QuattroScraper("quattro_shibuya").parse(
        _load("quattro_shibuya_live.html"), month=JUL)
    assert len(evs) == 33
    e = next(e for e in evs if "JUDA" in e.title_ja)
    assert e.source_url.endswith("/shibuya/schedule/detail/?cd=018163")
    assert e.start_date == "2026-07-01"
    assert (e.open_time, e.start_time) == ("16:45", "17:30")  # 開場/開演
    assert e.price_min == 5800                                # 前売 tier
    assert "SEX MACHINEGUNS" in e.lineup


def test_www_splits_halls_and_open_start_pairs():
    www_list = WWWScraper("www").parse(
        _load("www_schedule_live.html"), month=JUL)
    wwwx_list = WWWScraper("www_x").parse(
        _load("www_schedule_live.html"), month=JUL)
    assert len(www_list) == 28 and len(wwwx_list) == 28
    www = {e.start_date: e for e in www_list}
    wwwx = {e.start_date: e for e in wwwx_list}
    assert www["2026-07-01"].title_ja == "PRSMIN / キングサリ"
    assert wwwx["2026-07-01"].title_ja == "OXXXYMIRON"
    # "OPEN / START 18:30 / 19:30" = two distinct times, not one
    assert (wwwx["2026-07-01"].open_time,
            wwwx["2026-07-01"].start_time) == ("18:30", "19:30")
    assert wwwx["2026-07-01"].venue_name == "WWW X"


def test_duo_month_page_events_and_price_zones():
    evs = {e.source_url.split("#")[-1]: e
           for e in DuoScraper().parse(_load("duo_month_live.html"),
                                       month=JUL)}
    assert len(evs) == 27
    e = evs["260701"]
    assert (e.title_ja, e.subtitle) == ("XY", "UNTITLED")
    assert (e.open_time, e.start_time) == ("18:00", "19:00")
    assert e.price_min == 7700
    # drink charge (¥600) must not leak into price_min
    assert evs["260704"].price_min == 6000


def test_loft_and_shelter_parse_with_variant_hrefs():
    loft = LoftScraper("loft_shinjuku").parse(
        _load("loft_live.html"), today=dt.date(2026, 7, 12))
    shelter = LoftScraper("shelter").parse(
        _load("shelter_live.html"), today=dt.date(2026, 7, 12))
    assert len(loft) == 16 and len(shelter) == 22
    ska = next(e for e in loft if "SKA CRASH" in e.title_ja)
    assert ska.start_date == "2026-07-12"
    assert (ska.open_time, ska.start_time) == ("13:30", "14:30")
    # SHELTER links lack the second /schedule/ segment
    assert all("/schedule/shelter/" in e.source_url for e in shelter)
    red = next(e for e in shelter if "REDNECKS" in e.title_ja)
    assert (red.start_date, red.open_time) == ("2026-07-12", "12:00")


def test_yokohama_arena_json_feed():
    from tokyo_events.scrapers.yokohama_arena import YokohamaArenaScraper
    evs = YokohamaArenaScraper().parse(_load("yokohama_arena_202607.json"))
    assert len(evs) == 19
    e = evs[0]
    assert e.title_ja.startswith("We're timelesz")
    assert e.lineup == ["timelesz"]
    assert (e.start_date, e.open_time, e.start_time) == \
        ("2026-07-01", "11:30", "12:30")   # first stage of ①② pair
    assert e.source_url.endswith("#2026-07-01")   # per-day uniqueness
    assert e.ticket_url and e.ticket_url.startswith("http")


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


# ------------------------------------------- enrichment preservation (db)
def test_upsert_keeps_detail_fields_when_listing_regresses(tmp_path):
    store = EventStore(tmp_path / "merge.db")
    full = Event(source="x", source_url="https://x/1", title_ja="A",
                 start_date="2099-01-01", start_time="19:00",
                 price_min=5000, price_text="¥5,000",
                 ticket_links=[{"provider": "eplus",
                                "url": "https://eplus.jp/x", "code": None}])
    assert store.upsert(full) == "new"

    bare = Event(source="x", source_url="https://x/1", title_ja="A",
                 start_date="2099-01-01")
    # detail fields merge back in -> nothing actually changed
    assert store.upsert(bare) == "unchanged"
    stored = store.list_events()[0]
    assert stored["start_time"] == "19:00"
    assert stored["price_min"] == 5000
    assert stored["ticket_links"]


def test_upsert_merge_still_detects_real_changes(tmp_path):
    store = EventStore(tmp_path / "merge2.db")
    store.upsert(Event(source="x", source_url="https://x/1", title_ja="A",
                       start_date="2099-01-01", start_time="19:00"))
    moved = Event(source="x", source_url="https://x/1", title_ja="A",
                  start_date="2099-01-02")          # date really moved
    assert store.upsert(moved) == "changed"
    stored = store.list_events()[0]
    assert stored["start_date"] == "2099-01-02"
    assert stored["start_time"] == "19:00"          # enrichment survives


# --------------------------------------------- detail backlog (pipeline)
def test_pipeline_drains_detail_backlog_when_unchanged(tmp_path, monkeypatch):
    from tokyo_events import pipeline
    from tokyo_events.scrapers.base import BaseScraper

    from tokyo_events.models import Category

    def _bare():
        return Event(source="dummy", source_url="https://d/1", title_ja="A",
                     category=Category.MUSIC, start_date="2099-01-01")

    class DummyScraper(BaseScraper):
        source_id = "dummy"

        def scrape(self):
            yield _bare()

        def parse(self, html, **context):
            return [_bare()]

        def fetch(self, url, retries=2):        # no network in tests
            return ('<html><body>OPEN 18:00 START 19:00 '
                    '<p>前売 ¥4,000</p>'
                    '<a href="https://eplus.jp/sf/x">eplus</a>'
                    '</body></html>')

    store = EventStore(tmp_path / "backlog.db")
    assert store.upsert(_bare()) == "new"       # stored, missing details

    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (DummyScraper, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"])[0]

    # listing parse was 'unchanged', yet the backlog got enriched
    assert report["error"] is None
    assert report["unchanged"] == 1
    assert report["details"] == 1
    stored = store.list_events()[0]
    assert (stored["open_time"], stored["start_time"]) == ("18:00", "19:00")
    assert stored["price_min"] == 4000
    assert any(l["provider"] == "eplus" for l in stored["ticket_links"])


# ------------------------------------------------- non-music classifier
def test_nonmusic_classifier():
    assert tu.is_nonmusic("ディズニー・オン・アイス “Let's Party!”")
    assert tu.is_nonmusic("STARS ON ICE JAPAN TOUR 2027")
    assert tu.is_nonmusic("式典")
    assert tu.is_nonmusic("第43回 マイナビ 東京ガールズコレクション")
    assert tu.is_nonmusic("ぴあ Presents エンタメ業界研究フェス VOL.2")
    # concerts must never match
    assert not tu.is_nonmusic("King Gnu CEN+RAL Tour 2026")
    assert not tu.is_nonmusic("@JAM EXPO 2026 supported by UP-T")
    assert not tu.is_nonmusic("ヨルシカ LIVE TOUR 2026「一人称」")
    assert not tu.is_nonmusic("BABYMONSTER WORLD TOUR [춤(CHOOM)] IN JAPAN")


def test_yokohama_arena_marks_nonmusic_rows_other(tmp_path):
    from tokyo_events.scrapers.yokohama_arena import YokohamaArenaScraper
    import json as _json
    rows = [
        {"title": "ディズニー・オン・アイス", "artist": "",
         "date1": "2099-08-20", "path": "/event/detail/9999"},
        {"title": "ROCK BAND LIVE 2099", "artist": "The Band",
         "date1": "2099-08-21", "path": "/event/detail/8888"},
    ]
    evs = YokohamaArenaScraper().parse(_json.dumps(rows))
    by_date = {e.start_date: e for e in evs}
    assert by_date["2099-08-20"].category.value == "other"
    assert by_date["2099-08-21"].category.value == "music"


# --------------------------------------------------- response encoding
def test_pick_encoding_prefers_declared_over_detection():
    from tokyo_events.scrapers.base import pick_encoding
    head = b'<html><head><meta charset="utf-8"></head>'
    # detection says cyrillic (the toyosu mojibake incident) but the
    # document declares utf-8 -> declaration wins
    assert pick_encoding("text/html", head, "windows-1251", "ISO-8859-1") \
        == "utf-8"
    # header charset short-circuits everything
    assert pick_encoding("text/html; charset=Shift_JIS", head,
                         "windows-1251", "shift_jis") == "shift_jis"
    # nothing declared -> detection
    assert pick_encoding("text/html", b"<html>", "utf-8", None) == "utf-8"
    # bogus meta charset -> detection
    assert pick_encoding("text/html", b'<meta charset="notreal">',
                         "utf-8", None) == "utf-8"


# --------------------------------------------- zepp month pagination page
def test_zepp_live_month_page_parses():
    """Real September page fetched via ?_y=2026&_m=9 — the month-nav URL
    pattern scrape() walks. Structure must match the default page."""
    evs = ZeppScraper("zepp_divercity").parse(
        _load("zepp_schedule_month_live.html"), today=dt.date(2026, 7, 13))
    assert len(evs) == 14
    assert all(e.start_date and e.start_date.startswith("2026-09")
               for e in evs)
    sweet = next(e for e in evs if e.title_ja == "Sweet Alley")
    assert sweet.source_url.startswith(
        "https://www.zepp.co.jp/hall/divercity/schedule/single/?rid=")
    assert sweet.start_time is not None


# ----------------------------------------------------- registry invariants
def test_registry_every_factory_constructs_and_is_polite():
    from tokyo_events import pipeline
    assert len(pipeline.SCRAPERS) >= 53
    for sid, (factory, status) in pipeline.SCRAPERS.items():
        s = factory()
        assert s.source_id == sid, f"{sid}: source_id mismatch ({s.source_id})"
        assert s.rate_limit_s >= 2, f"{sid}: rate limit below politeness floor"
        assert callable(s.scrape) and callable(s.parse)


# ------------------------------------------------------ drink-charge strip
def test_strip_drink_charges_protects_min_price():
    cases = [
        "ADV ¥5,500（税込・ドリンク代別途￥600）",
        "前売 ¥5,500 ※別途ドリンク￥600",
        "ADV ¥5,500 +1DRINK ¥600",
        "¥5,500 (D代600円)",
    ]
    for text in cases:
        _, pmin, _ = tu.parse_prices(tu.strip_drink_charges(text))
        assert pmin == 5500, text
    # a genuinely cheap show must not be stripped
    _, pmin, _ = tu.parse_prices(tu.strip_drink_charges("前売 ¥800"))
    assert pmin == 800


# ------------------------------------------------- review-pass regressions
def test_rejected_status_survives_content_changes(tmp_path):
    store = EventStore(tmp_path / "rej.db")
    ev = Event(source="x", source_url="https://x/1", title_ja="spam",
               start_date="2099-01-01")
    store.upsert(ev)
    store.set_status(ev.dedupe_key(), ReviewStatus.REJECTED)
    changed = Event(source="x", source_url="https://x/1", title_ja="spam",
                    start_date="2099-01-01", is_sold_out=True)
    assert store.upsert(changed) == "changed"
    row = store.conn.execute("SELECT status FROM events").fetchone()
    assert row["status"] == "rejected"      # human curation is sticky


def test_fully_failed_detail_pass_is_loud(tmp_path, monkeypatch):
    from tokyo_events import pipeline
    from tokyo_events.models import Category
    from tokyo_events.scrapers.base import BaseScraper

    def _bare(i):
        return Event(source="dummy", source_url=f"https://d/{i}",
                     title_ja=f"E{i}", category=Category.MUSIC,
                     start_date="2099-01-01")

    class BrokenDetail(BaseScraper):
        source_id = "dummy"

        def scrape(self):
            yield from (_bare(i) for i in range(3))

        def parse(self, html, **context):
            return []

        def fetch(self, url, retries=2):
            raise RuntimeError("403 on every detail page")

    store = EventStore(tmp_path / "loud.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (BrokenDetail, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"])[0]
    assert report["found"] == 3
    assert report["details"] == 0
    assert report["error"] and "detail pass failed" in report["error"]


def test_strip_drink_charges_bare_betsu_forms():
    for text in ("前売 ¥3,000 ドリンク代別 ¥600",
                 "前売 ¥3,000 ドリンク別 ¥600",
                 "前売 ¥3,000 1ドリンク代別 ¥600"):
        _, pmin, _ = tu.parse_prices(tu.strip_drink_charges(text))
        assert pmin == 3000, text


def test_nonmusic_short_tokens_do_not_hide_concerts():
    # substring traps found in review
    assert not tu.is_nonmusic("WORK-1st Anniversary LIVE")
    assert not tu.is_nonmusic("NETWORK-1 TOUR")
    assert not tu.is_nonmusic("豆腐プロレス THE REAL 2026")   # AKB48 project
    # real combat-sports events still classify
    assert tu.is_nonmusic("新日本プロレス 東京大会")
    assert tu.is_nonmusic("K-1 WORLD GP 2026")
    assert tu.is_nonmusic("RIZIN LANDMARK 12")
