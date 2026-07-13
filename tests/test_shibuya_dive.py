import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.models import Category
from tokyo_events.scrapers.shibuya_dive import ShibuyaDiveScraper

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _events():
    return ShibuyaDiveScraper().parse(_load("shibuya_dive_live.html"))


def _by_title(evs, needle):
    return next(e for e in evs if needle in (e.title_ja or ""))


def test_parses_every_article():
    # 14 <article class="schedule-article"> blocks on the July page.
    assert len(_events()) == 14


def test_first_event_fields():
    e = _by_title(_events(), "びっくえんじぇる不定期公演")
    assert e.title_ja == "びっくえんじぇる不定期公演まんぷく祭in東京"
    assert e.start_date == "2026-07-12"
    assert (e.open_time, e.start_time) == ("14:00", "15:00")
    assert e.lineup == ["びっくえんじぇる"]
    assert e.category == Category.MUSIC
    assert e.venue_name == "SHIBUYA DIVE" and e.venue_area == "Shibuya"


def test_multi_artist_act_and_loose_adv_label():
    # LIVEPLANET has START 18:30 then an ADV row holding a *time* (19:00),
    # not a price. The loose ADV label must NOT leak into price_min, and no
    # ¥ appears in the listing at all.
    e = _by_title(_events(), "LIVEPLANET")
    assert e.title_ja == "『LIVEPLANET FREE LIVE』"
    assert e.start_date == "2026-07-14"
    assert e.start_time == "18:30"
    assert e.open_time is None            # no OPEN row on this block
    assert e.price_min is None and e.price_text is None
    assert e.lineup == ["AISTAL", "マイノリティアラート", "CURE'T", "MeMeQ"]


def test_detail_urls_are_absolute_https_on_venue_domain():
    evs = _events()
    assert all(e.source_url.startswith("https://shibuya-dive.com/schedule/")
               for e in evs)
    # slugs are unique -> dedupe by source_url keeps all 14
    assert len({e.source_url for e in evs}) == 14


def test_empty_html_yields_nothing_loud_failure():
    assert ShibuyaDiveScraper().parse("<html></html>") == []


def test_month_links_from_sidebar_exclude_active_month():
    # Sidebar lists 2026-07..2027-01; the bare page already served 2026-07
    # (td.month_name = "2026.07"), so it must be dropped.
    links = ShibuyaDiveScraper()._month_links(_load("shibuya_dive_live.html"))
    assert links == [
        "https://shibuya-dive.com/schedule/?date=2026-08",
        "https://shibuya-dive.com/schedule/?date=2026-09",
        "https://shibuya-dive.com/schedule/?date=2026-10",
        "https://shibuya-dive.com/schedule/?date=2026-11",
        "https://shibuya-dive.com/schedule/?date=2027-01",
    ]
