import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events.promoters import apply_promoter_merge


def _venue_ev(**kw):
    d = {"source": "zepp_shinjuku", "source_url": "https://zepp/1",
         "title_ja": "who killed paledusk TOUR", "title_en": None,
         "start_date": "2026-07-15", "venue_name": "Zepp Shinjuku (TOKYO)",
         "lineup": ["Paledusk"], "ticket_links": [], "is_sold_out": False,
         "open_time": None, "start_time": None, "price_min": None,
         "price_text": None, "is_free": None, "ticket_url": None}
    d.update(kw)
    return d


def _promo_ev(**kw):
    d = {"source": "creativeman", "source_url": "https://cm/paledusk/#2026-07-15",
         "title_ja": "Paledusk", "title_en": None,
         "start_date": "2026-07-15", "venue_name": "Zepp Shinjuku(TOKYO)",
         "lineup": ["Paledusk"], "is_sold_out": True,
         "open_time": "18:00", "start_time": "19:00", "price_min": 5800,
         "price_text": "スタンディング ￥5,800", "is_free": False,
         "ticket_url": None,
         "ticket_links": [{"provider": "pia", "url": "https://w.pia.jp/x",
                           "code": None}]}
    d.update(kw)
    return d


def test_duplicate_promoter_row_merges_into_venue_event():
    venue, promo = _venue_ev(), _promo_ev()
    out = apply_promoter_merge([venue, promo])
    assert out == [venue]                       # promoter row folded away
    assert venue["is_sold_out"] is True         # promoter badge carried over
    assert venue["open_time"] == "18:00"        # gap-filled
    assert venue["price_min"] == 5800
    assert any(t["provider"] == "pia" for t in venue["ticket_links"])
    assert venue["venue_key"] == "zepp_shinjuku"


def test_promoter_event_at_gap_venue_stays():
    promo = _promo_ev(venue_name="日本武道館",
                      source_url="https://sogo/detail/2577",
                      source="sogo_tokyo", title_ja="山本彩 LIVE at 武道館",
                      lineup=["山本彩"])
    out = apply_promoter_merge([promo])
    assert out == [promo]
    assert promo["venue_key"] == "budokan"


def test_same_venue_same_day_different_artist_not_merged():
    venue = _venue_ev(title_ja="全く別のバンドのワンマン", lineup=["別のバンド"])
    promo = _promo_ev()
    out = apply_promoter_merge([venue, promo])
    assert len(out) == 2                        # both kept — no false merge
    assert promo["venue_key"] == "zepp_shinjuku"


def test_merge_never_overwrites_venue_facts():
    venue = _venue_ev(open_time="17:00", start_time="18:00", price_min=6000)
    promo = _promo_ev()                         # says 18:00/19:00 ¥5,800
    apply_promoter_merge([venue, promo])
    assert (venue["open_time"], venue["start_time"]) == ("17:00", "18:00")
    assert venue["price_min"] == 6000           # venue is authoritative


def test_unresolved_promoter_venue_is_skipped_from_export():
    """A promoter row with no displayable venue — unresolved string or a
    listing-level placeholder (venue_name None) — must not export; keeping
    it made the promoter itself appear as a venue on the site."""
    promo = _promo_ev(venue_name="謎の新会場XYZ")
    placeholder = _promo_ev(venue_name=None,
                            source_url="https://cm.example/tour2/#2026-09-01")
    out = apply_promoter_merge([promo, placeholder])
    assert out == []


def _fest_ev(**kw):
    d = {"source": "festivals", "source_url": "https://fes/#2026-08-15",
         "title_ja": "SUMMER SONIC 2026", "title_en": None,
         "start_date": "2026-08-15", "end_date": None,
         "venue_name": "SUMMER SONIC (TOKYO)", "lineup": ["FALL OUT BOY"],
         "ticket_links": [], "is_sold_out": False}
    d.update(kw)
    return d


def test_festival_events_get_festival_venue_key():
    fest = _fest_ev()
    out = apply_promoter_merge([fest])
    assert out == [fest]
    assert fest["venue_key"] == "summer_sonic_tokyo"


def test_host_venue_row_folds_into_festival():
    fest = _fest_ev()
    messe = _venue_ev(source="makuhari_messe",
                      title_ja="SUMMER SONIC 2026",
                      venue_name="幕張メッセ", start_date="2026-08-15",
                      lineup=[])
    out = apply_promoter_merge([fest, messe])
    assert out == [fest]                    # host-venue duplicate folded


def test_afterparty_at_club_is_not_folded():
    fest = _fest_ev()
    club = _venue_ev(source="www", title_ja="SUMMER SONIC AFTER PARTY",
                     venue_name="WWW", start_date="2026-08-15", lineup=[])
    out = apply_promoter_merge([fest, club])
    assert len(out) == 2                    # club show survives


def test_promoter_row_for_festival_folds():
    fest = _fest_ev()
    promo = _promo_ev(title_ja="SUMMER SONIC 2026",
                      venue_name="幕張メッセ", start_date="2026-08-15",
                      lineup=[])
    out = apply_promoter_merge([fest, promo])
    assert out == [fest]
