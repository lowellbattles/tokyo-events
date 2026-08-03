import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokyo_events import ical


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def _ev(**kw):
    """A synthetic event dict matching the site/public.json feed contract
    (see CLAUDE.md / db.py's export_public_json)."""
    d = {
        "source": "liquidroom", "source_url": "https://liquidroom.com/e/1",
        "title_ja": "テストイベント", "title_en": None,
        "category": "music", "genres": [],
        "start_date": "2026-08-15", "end_date": None,
        "open_time": None, "start_time": None,
        "venue_name": "Liquidroom", "venue_key": "liquidroom",
        "price_min": None, "is_free": None, "is_sold_out": False,
        "artists": [],
    }
    d.update(kw)
    return d


def _uid_of(ev: dict) -> str:
    """Independent reimplementation of the UID recipe (sha256 of
    "source|source_url", 16 hex chars, + "@tokyo-events" suffix) -- the
    same recipe as models.Event.dedupe_key(), computed here via hashlib
    directly rather than by importing ical's internals."""
    raw = f"{ev['source']}|{ev['source_url']}".encode()
    return hashlib.sha256(raw).hexdigest()[:16] + "@tokyo-events"


def _reference_escape(s: str) -> str:
    """Independent per-character reference implementation of RFC 5545
    3.3.11 TEXT escaping, structurally different from ical.py's
    sequential global-replace approach, used to derive expected values
    without leaning on the implementation under test."""
    out = []
    for ch in s.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\\":
            out.append("\\\\")
        elif ch == ";":
            out.append("\\;")
        elif ch == ",":
            out.append("\\,")
        elif ch == "\n":
            out.append("\\n")
        else:
            out.append(ch)
    return "".join(out)


def _physical_lines(ics_text: str) -> list[str]:
    """Raw physical lines (post-fold), as actually written to disk."""
    lines = ics_text.split("\r\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # trailing CRLF produces one empty artifact
    return lines


def _unfold(ics_text: str) -> list[str]:
    """Reverse RFC 5545 line folding: a continuation line starts with a
    single SPACE, which is removed as it's rejoined onto the previous
    logical line."""
    logical = []
    for line in _physical_lines(ics_text):
        if line.startswith(" ") and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return logical


def _lines_starting(logical_lines, prefix):
    return [l for l in logical_lines if l.startswith(prefix)]


def _find_line(logical_lines, prefix):
    matches = _lines_starting(logical_lines, prefix)
    assert len(matches) == 1, f"expected exactly one {prefix!r} line, got {matches}"
    return matches[0]


# --------------------------------------------------------------------------
# escaping
# --------------------------------------------------------------------------

def test_escaping_backslash_semicolon_comma_newline():
    raw_title = "Show: A, B; C\\D\nEncore"
    text = ical.build_calendar([_ev(title_ja=raw_title)], "Test")
    logical = _unfold(text)
    assert _find_line(logical, "SUMMARY:") == "SUMMARY:" + _reference_escape(raw_title)


def test_line_endings_are_strictly_crlf():
    # a raw newline in the input must come out as literal "\n" TEXT, never
    # as an actual newline byte in the output -- CRLF is reserved for
    # content-line/fold terminators.
    text = ical.build_calendar([_ev(title_ja="Line\nBreak")], "Test")
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")


# --------------------------------------------------------------------------
# UTF-8 aware line folding
# --------------------------------------------------------------------------

def test_utf8_line_folding_long_japanese_title():
    title = "あ" * 40  # 3 bytes/char in UTF-8 -> 120 bytes, over the 75 limit
    text = ical.build_calendar([_ev(title_ja=title, venue_name=None)], "Test")

    physical = _physical_lines(text)
    assert len(physical) > 1, "expected folding to have split at least one line"
    for line in physical:
        nbytes = len(line.encode("utf-8"))
        assert nbytes <= 75, f"line exceeds 75 octets ({nbytes}): {line!r}"

    logical = _unfold(text)
    assert _find_line(logical, "SUMMARY:") == "SUMMARY:" + title


# --------------------------------------------------------------------------
# DTSTART / DTEND shapes
# --------------------------------------------------------------------------

def test_timed_event_uses_tzid_dtstart_and_omits_dtend():
    ev = _ev(start_date="2026-08-15", start_time="19:00")
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert "DTSTART;TZID=Asia/Tokyo:20260815T190000" in logical
    assert not _lines_starting(logical, "DTEND")


def test_allday_single_day_dtend_is_start_plus_one():
    ev = _ev(start_date="2026-08-15", start_time=None, end_date=None)
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert "DTSTART;VALUE=DATE:20260815" in logical
    assert "DTEND;VALUE=DATE:20260816" in logical


def test_allday_multiday_dtend_is_end_date_plus_one_exclusive():
    ev = _ev(start_date="2026-08-15", end_date="2026-08-17", start_time=None)
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert "DTSTART;VALUE=DATE:20260815" in logical
    assert "DTEND;VALUE=DATE:20260818" in logical


def test_build_calendar_skips_events_without_start_date():
    ev_ok = _ev(source="a", source_url="https://a/1", start_date="2026-08-01")
    ev_bad = _ev(source="b", source_url="https://b/1", start_date=None)
    logical = _unfold(ical.build_calendar([ev_ok, ev_bad], "Test"))
    uids = [l[len("UID:"):] for l in logical if l.startswith("UID:")]
    assert uids == [_uid_of(ev_ok)]


# --------------------------------------------------------------------------
# SUMMARY / sold-out marker
# --------------------------------------------------------------------------

def test_sold_out_appends_marker_to_summary():
    ev = _ev(title_ja="Great Show", title_en=None, is_sold_out=True)
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert _find_line(logical, "SUMMARY:") == "SUMMARY:Great Show [SOLD OUT]"


def test_summary_falls_back_to_title_en():
    ev = _ev(title_ja=None, title_en="English Title")
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert _find_line(logical, "SUMMARY:") == "SUMMARY:English Title"


# --------------------------------------------------------------------------
# LOCATION / DESCRIPTION / URL
# --------------------------------------------------------------------------

def test_location_description_and_url_present():
    ev = _ev(venue_name="Zepp Shinjuku (TOKYO)",
              artists=["Artist A", "Artist B"], price_min=5800,
              source_url="https://zepp.co.jp/events/1")
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert _find_line(logical, "LOCATION:") == "LOCATION:Zepp Shinjuku (TOKYO)"
    # the artist-joining comma AND the price's thousands-separator comma
    # both get escaped, since the whole DESCRIPTION value is one TEXT block
    assert (_find_line(logical, "DESCRIPTION:")
            == "DESCRIPTION:Artist A\\, Artist B\\n¥5\\,800~")
    assert _find_line(logical, "URL:") == "URL:https://zepp.co.jp/events/1"


def test_is_free_used_when_no_price_min():
    ev = _ev(is_free=True, price_min=None, artists=[])
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert _find_line(logical, "DESCRIPTION:") == "DESCRIPTION:Free"


def test_omits_location_description_url_when_absent_or_non_http():
    ev = _ev(venue_name=None, artists=[], price_min=None, is_free=None,
              source_url="not-a-url")
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert not _lines_starting(logical, "LOCATION:")
    assert not _lines_starting(logical, "DESCRIPTION:")
    assert not _lines_starting(logical, "URL:")


# --------------------------------------------------------------------------
# UID
# --------------------------------------------------------------------------

def test_uid_matches_dedupe_key_recipe():
    ev = _ev(source="zepp_shinjuku", source_url="https://zepp/events/42")
    expected = (hashlib.sha256(b"zepp_shinjuku|https://zepp/events/42")
                .hexdigest()[:16] + "@tokyo-events")
    logical = _unfold(ical.build_calendar([ev], "Test"))
    assert _find_line(logical, "UID:") == f"UID:{expected}"


# --------------------------------------------------------------------------
# DTSTAMP / now
# --------------------------------------------------------------------------

def test_dtstamp_uses_provided_now_converted_to_utc():
    jst_noon = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    logical = _unfold(ical.build_calendar([_ev()], "Test", now=jst_noon))
    assert _find_line(logical, "DTSTAMP:") == "DTSTAMP:20260803T030000Z"


# --------------------------------------------------------------------------
# VCALENDAR header + VTIMEZONE
# --------------------------------------------------------------------------

def test_header_fields_and_vtimezone_block():
    events = [_ev(source="a", source_url="https://a/1", start_date="2026-08-01"),
              _ev(source="b", source_url="https://b/1", start_date="2026-08-02")]
    text = ical.build_calendar(events, "My Calendar")

    assert text.count("BEGIN:VTIMEZONE") == 1
    assert text.count("END:VTIMEZONE") == 1

    logical = _unfold(text)
    assert "VERSION:2.0" in logical
    assert "PRODID:-//tokyo-events//JP" in logical
    assert "CALSCALE:GREGORIAN" in logical
    assert _find_line(logical, "X-WR-CALNAME:") == "X-WR-CALNAME:My Calendar"
    assert "X-WR-TIMEZONE:Asia/Tokyo" in logical
    assert "TZID:Asia/Tokyo" in logical
    assert "BEGIN:STANDARD" in logical
    assert "DTSTART:19700101T000000" in logical
    assert "TZOFFSETFROM:+0900" in logical
    assert "TZOFFSETTO:+0900" in logical
    assert "END:STANDARD" in logical


# --------------------------------------------------------------------------
# deterministic ordering
# --------------------------------------------------------------------------

def test_deterministic_ordering_by_date_then_time_then_uid():
    ev_late = _ev(source="a", source_url="https://a/1",
                   start_date="2026-08-02", start_time="10:00")
    ev_early_notime = _ev(source="b", source_url="https://b/1",
                           start_date="2026-08-01", start_time=None)
    ev_early_timed = _ev(source="c", source_url="https://c/1",
                          start_date="2026-08-01", start_time="09:00")
    events = [ev_late, ev_early_notime, ev_early_timed]

    expected_order = sorted(
        events, key=lambda e: (e["start_date"], e["start_time"] or "", _uid_of(e)))
    expected_uids = [_uid_of(e) for e in expected_order]

    fixed_now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    text = ical.build_calendar(events, "Test", now=fixed_now)
    logical = _unfold(text)
    actual_uids = [l[len("UID:"):] for l in logical if l.startswith("UID:")]
    assert actual_uids == expected_uids

    # order-independence: reversed input, same pinned `now` -> byte-identical output
    text2 = ical.build_calendar(list(reversed(events)), "Test", now=fixed_now)
    assert text2 == text


# --------------------------------------------------------------------------
# build_all: category filter, start_date guard, per-venue fan-out
# --------------------------------------------------------------------------

def test_build_all_fans_out_per_venue_and_filters_category_and_missing_dates():
    ev1 = _ev(source="liquidroom", source_url="https://liquidroom/1",
               venue_key="liquidroom", start_date="2026-08-01")
    ev2 = _ev(source="oeast", source_url="https://oeast/1",
               venue_key="oeast", start_date="2026-08-02")
    ev3 = _ev(source="oeast", source_url="https://oeast/2",
               venue_key="oeast", start_date="2026-08-03")
    festival = _ev(source="festivals", source_url="https://ff/1",
                    venue_key="fuji_rock", category="music_festival",
                    start_date="2026-07-25")
    non_music = _ev(source="mori_art_museum", source_url="https://mori/1",
                     venue_key="mori_art_museum", category="art",
                     start_date="2026-08-01")
    no_date = _ev(source="liquidroom", source_url="https://liquidroom/2",
                   venue_key="liquidroom", start_date=None)

    result = ical.build_all([ev1, ev2, ev3, festival, non_music, no_date])

    assert set(result.keys()) == {
        "events.ics", "ical/liquidroom.ics", "ical/oeast.ics", "ical/fuji_rock.ics",
    }

    def uids_in(rel_path):
        return {l[len("UID:"):] for l in _unfold(result[rel_path])
                if l.startswith("UID:")}

    # music + music_festival only; art and the undated event are dropped
    assert uids_in("events.ics") == {_uid_of(ev1), _uid_of(ev2), _uid_of(ev3),
                                      _uid_of(festival)}
    assert uids_in("ical/liquidroom.ics") == {_uid_of(ev1)}
    assert uids_in("ical/oeast.ics") == {_uid_of(ev2), _uid_of(ev3)}
    assert uids_in("ical/fuji_rock.ics") == {_uid_of(festival)}


def test_build_all_venue_key_falls_back_to_source_when_missing():
    ev = _ev(source="duo", source_url="https://duo/1", venue_key=None,
              start_date="2026-08-01")
    result = ical.build_all([ev])
    assert "ical/duo.ics" in result


def test_build_all_shares_one_dtstamp_across_all_files():
    ev1 = _ev(source="liquidroom", source_url="https://liquidroom/1",
               venue_key="liquidroom", start_date="2026-08-01")
    ev2 = _ev(source="oeast", source_url="https://oeast/1",
               venue_key="oeast", start_date="2026-08-02")
    fixed_now = datetime(2026, 7, 20, 3, 4, 5, tzinfo=timezone.utc)
    result = ical.build_all([ev1, ev2], now=fixed_now)
    for text in result.values():
        dtstamps = _lines_starting(_unfold(text), "DTSTAMP:")
        assert dtstamps  # every file has at least one VEVENT
        assert set(dtstamps) == {"DTSTAMP:20260720T030405Z"}


def test_build_all_no_relevant_events_still_returns_events_ics():
    non_music = _ev(category="art", start_date="2026-08-01")
    result = ical.build_all([non_music])
    assert set(result.keys()) == {"events.ics"}
    assert _unfold(result["events.ics"])[0] == "BEGIN:VCALENDAR"
