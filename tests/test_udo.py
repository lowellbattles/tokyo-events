import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.udo import UdoArtistsScraper, parse_show, parse_shows

FIX = Path(__file__).parent / "fixtures"

# GLAY "EXOFIRE" arena tour: single "東京" tab, both dates at 有明アリーナ
# (Ariake Arena) — a venue venues.resolve_venue already knows, so this is
# our one fully-resolving fixture.
GLAY_URL = "https://www.udo.jp/shows/GLAY_arenatour2026_27"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _glay():
    return parse_show(_load("udo_artists_detail_live.html"), GLAY_URL)


# ----------------------------------------------------------------- listing
def test_listing_extracts_every_show_slug():
    # /shows lists 12 tour cards, one per /shows/<slug> detail page.
    urls = parse_shows(_load("udo_artists_live.html"))
    assert len(urls) == 12
    assert all(u.startswith("https://www.udo.jp/shows/") for u in urls)
    assert GLAY_URL in urls


def test_listing_urls_are_deduped_and_absolute():
    urls = parse_shows(_load("udo_artists_live.html"))
    assert len(urls) == len(set(urls))


# ------------------------------------------------------------------ detail
def test_kanto_leg_parses_venue_date_and_times():
    evs = _glay()
    assert len(evs) == 2
    by_date = {e.start_date: e for e in evs}

    fri = by_date["2026-11-28"]
    assert fri.venue_name == "有明アリーナ"
    assert fri.open_time == "16:00"
    assert fri.start_time == "17:00"
    assert fri.title_ja == "GLAY"
    assert fri.lineup == ["GLAY"]
    assert fri.category == Category.MUSIC
    assert fri.source == "udo_artists"
    assert fri.source_url == f"{GLAY_URL}#2026-11-28"

    sat = by_date["2026-11-29"]
    assert sat.venue_name == "有明アリーナ"
    assert sat.open_time == "15:00"
    assert sat.start_time == "16:00"


def test_kanto_leg_gets_price_and_ticket_links():
    evs = _glay()
    fri = next(e for e in evs if e.start_date == "2026-11-28")
    # "S席 ￥11,000（税込） / A席 ￥9,000（税込）" -> lowest tier.
    assert fri.price_min == 9000
    assert fri.is_free is False
    providers = {link["provider"] for link in fri.ticket_links}
    assert providers == {"lawson"}


def test_both_legs_share_the_tabs_price_and_links():
    evs = _glay()
    fri, sat = (e for e in sorted(evs, key=lambda e: e.start_date))
    assert fri.price_min == sat.price_min
    assert fri.ticket_links == sat.ticket_links


def test_unresolved_venue_tab_yields_nothing():
    # A schedule item whose venue string venues.resolve_venue() doesn't
    # know (even under a "東京"-flavoured show) must NOT be trusted just
    # because it looks Kanto -- the venue string is the fact, not the tab
    # label. Nothing resolves here, so parse_show returns no events at all.
    html = """
    <html><body>
      <h1 class="s-showsDetail__title"><div><p>TEST ARTIST</p></div></h1>
      <div class="s-showsDetail__scheduleContent">
        <ul class="s-showsDetail__scheduleList">
          <li class="s-showsDetail__scheduleItem">
            <div class="s-showsDetail__scheduleCard">
              <p class="s-showsDetail__scheduleDate">
                <span class="s-showsDetail__scheduleDateYear">2026</span>年<span class="s-showsDetail__scheduleDateNum">10</span>月<span class="s-showsDetail__scheduleDateNum">15</span>日
              </p>
              <p class="s-showsDetail__scheduleVenue">大阪城ホール</p>
              <div class="s-showsDetail__scheduleTimeWrap">
                <p class="s-showsDetail__scheduleTime">18:00 open</p>
                <p class="s-showsDetail__scheduleTime">19:00 start</p>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </body></html>
    """
    assert parse_show(html, "https://www.udo.jp/shows/test") == []


def test_unresolved_venue_is_collected_when_tracked():
    skipped: set[str] = set()
    html = """
    <html><body>
      <h1 class="s-showsDetail__title"><div><p>TEST ARTIST</p></div></h1>
      <div class="s-showsDetail__scheduleContent">
        <ul class="s-showsDetail__scheduleList">
          <li class="s-showsDetail__scheduleItem">
            <p class="s-showsDetail__scheduleDate">
              <span class="s-showsDetail__scheduleDateYear">2026</span>年<span class="s-showsDetail__scheduleDateNum">10</span>月<span class="s-showsDetail__scheduleDateNum">15</span>日
            </p>
            <p class="s-showsDetail__scheduleVenue">大阪城ホール</p>
          </li>
        </ul>
      </div>
    </body></html>
    """
    evs = parse_show(html, "https://www.udo.jp/shows/test", skipped=skipped)
    assert evs == []
    assert skipped == {"大阪城ホール"}


def test_empty_html_returns_nothing_loudly():
    assert parse_shows("<html></html>") == []
    assert parse_shows("") == []
    assert parse_show("<html></html>", GLAY_URL) == []
    assert parse_show("", GLAY_URL) == []


def test_scraper_source_id():
    assert UdoArtistsScraper().source_id == "udo_artists"
    assert UdoArtistsScraper().supports_detail is False


def test_scraper_tracks_skipped_venues_across_shows():
    scraper = UdoArtistsScraper()
    html = """
    <html><body>
      <h1 class="s-showsDetail__title"><div><p>TEST ARTIST</p></div></h1>
      <div class="s-showsDetail__scheduleContent">
        <ul class="s-showsDetail__scheduleList">
          <li class="s-showsDetail__scheduleItem">
            <p class="s-showsDetail__scheduleDate">
              <span class="s-showsDetail__scheduleDateYear">2026</span>年<span class="s-showsDetail__scheduleDateNum">10</span>月<span class="s-showsDetail__scheduleDateNum">15</span>日
            </p>
            <p class="s-showsDetail__scheduleVenue">大阪城ホール</p>
          </li>
        </ul>
      </div>
    </body></html>
    """
    parse_show(html, "https://www.udo.jp/shows/test", skipped=scraper.skipped_venues)
    assert "大阪城ホール" in scraper.skipped_venues
