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


def test_unresolvable_promoter_venue_falls_back_to_source_key():
    promo = _promo_ev(venue_name="謎の新会場XYZ")
    out = apply_promoter_merge([promo])
    assert out == [promo]
    assert promo["venue_key"] == "creativeman"
