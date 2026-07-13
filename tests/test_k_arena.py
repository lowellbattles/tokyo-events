import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category, Event
from tokyo_events.scrapers.k_arena import KArenaScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _july():
    evs = KArenaScraper().parse(_load("k_arena_yokohama_live.html"))
    return {e.source_url: e for e in evs}


# ------------------------------------------------------------------- count
def test_parses_exact_count():
    # 13 <li class="schedule-list-item"> in the July fixture.
    assert len(_july()) == 13


# ---------------------------------------------------------- field spot-checks
def test_field_spotchecks():
    ev = _july()

    # AND2BLE — 2026.07.01, OPEN 17:30 / START 19:00 (ASCII title).
    a = ev["https://k-arena.com/schedule/20260701-1/"]
    assert a.title_ja == "2026 AND2BLE SHOW CONCERT : Welcome to Qurious In Japan"
    assert a.start_date == "2026-07-01"
    assert a.end_date is None
    assert (a.open_time, a.start_time) == ("17:30", "19:00")
    assert a.lineup == ["AND2BLE"]
    assert a.category == Category.MUSIC

    # 肉チョモランマ DAY2 — JA title + a headliner-plus-guests lineup.
    n = ev["https://k-arena.com/schedule/20260705-1/"]
    assert n.title_ja == "肉チョモランマ ワンマンライブ 2026『大決戦』 “DAY2：修羅場”"
    assert n.start_date == "2026-07-05"
    assert (n.open_time, n.start_time) == ("15:30", "17:00")
    assert n.lineup[0] == "肉チョモランマ"
    assert "そらる" in n.lineup

    # OFFICIAL HIGE DANDISM — 2026.07.25.
    h = ev["https://k-arena.com/schedule/20260725-1/"]
    assert h.title_ja == "OFFICIAL HIGE DANDISM one-man tour 2026"
    assert h.start_date == "2026-07-25"
    assert (h.open_time, h.start_time) == ("16:30", "18:00")


def test_lineup_drops_annotation_notes():
    # BEAT AX lists acts then a "※五十音順…" ordering note; the ※-note must
    # not leak into the artist lineup.
    beat = _july()["https://k-arena.com/schedule/20260710-1/"]
    assert "FANTASTICS" in beat.lineup
    assert all(not x.startswith("※") for x in beat.lineup)


# ---------------------------------------------------------- detail-URL / meta
def test_absolute_source_urls_and_venue_meta():
    for e in _july().values():
        assert e.source_url.startswith("https://k-arena.com/schedule/")
        assert e.source_url.endswith("/")
        assert e.venue_name == "Kアリーナ横浜"
        assert e.venue_area == "Minato Mirai"
        assert e.lat == 35.4655 and e.lng == 139.6293
        assert e.genres == []


def test_dates_are_unique_per_item():
    # Each list item is one single-day event with its own detail URL — no
    # collisions after dedupe by source_url.
    urls = [e.source_url for e in _july().values()]
    assert len(urls) == len(set(urls)) == 13


# -------------------------------------------------------------- category policy
def test_category_real_concert_is_music():
    # K-Arena is music-dedicated; every real July row is a concert.
    for e in _july().values():
        assert e.category == Category.MUSIC


def test_nonmusic_row_is_other():
    # Synthetic row with a clearly non-concert title (ice show) -> OTHER,
    # proving the is_nonmusic guard is wired in even on a music venue.
    html = (
        '<ul class="schedule-list"><li class="schedule-list-item">'
        '<p class="schedule-list-item__date">2026.12.24.Thu.</p>'
        '<a href="https://k-arena.com/schedule/20261224-1/">'
        '<h2 class="schedule-list-item__title">ディズニー・オン・アイス 2026</h2>'
        '<p class="schedule-list-item__artist">Disney On Ice</p>'
        '<div class="schedule-list-item__open-start"><p>OPEN 17:00 / START 18:00</p></div>'
        '</a></li></ul>'
    )
    out = KArenaScraper().parse(html)
    assert len(out) == 1
    assert out[0].category == Category.OTHER
    assert out[0].start_date == "2026-12-24"


# ------------------------------------------------------------- detail-page pass
def test_detail_price_single_tier():
    ev = Event(source="k_arena_yokohama",
               source_url="https://k-arena.com/schedule/20260701-1/")
    KArenaScraper().parse_detail(_load("k_arena_yokohama_detail.html"), ev)
    assert ev.price_min == 12800
    assert ev.is_free is False
    assert "12,800円" in ev.price_text
    # No standard playguide providers on K-Arena detail pages.
    assert ev.ticket_links == []


def test_detail_price_min_is_floor_not_component():
    # TICKETS zone = "◆VIP席：27,500円 … ※「指定席16,500円+アップグレード
    # 11,000円」 ◆指定席：16,500円 …". The floor is the 指定席 tier (16,500),
    # NOT the note-embedded 11,000 upgrade component nor the 27,500 VIP tier.
    ev = Event(source="k_arena_yokohama",
               source_url="https://k-arena.com/schedule/20260730-1/")
    KArenaScraper().parse_detail(
        _load("k_arena_yokohama_detail_lesserafim.html"), ev)
    assert ev.price_min == 16500


# ---------------------------------------------------------- loud structural fail
def test_empty_html_returns_nothing_loudly():
    assert KArenaScraper().parse("<html></html>") == []
    assert KArenaScraper().parse("") == []
