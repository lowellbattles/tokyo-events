#!/usr/bin/env python3
"""Report probable duplicate events in the exported feed (roadmap R5).

Groups current/future events by (start_date, venue_key) and prints the
groups where more than one source contributed a row — the class the
promoter merge is supposed to fold. Two rows here are either a merge
gap (fix promoters._artist_overlap / add a curated alias) or genuinely
distinct same-day events at one venue (fine).

Usage:
  python scripts/find_dupes.py [path/to/public.json]

Exit code is always 0 — this is a report, not a gate.
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokyo_events.promoters import (PROMOTER_SOURCES,  # noqa: E402
                                    _times_compatible, _titles_overlap)
from tokyo_events.scrapers.textutils import jst_today  # noqa: E402


def _is_performance_pairing(rows: list[dict]) -> bool:
    """True when a same-source group is explained as one show's multiple
    performances that day: titles align and every pair states a
    DIFFERENT start time (昼/夜公演 — the R6 fold correctly keeps
    these; they are schedule facts, not duplicates)."""
    return all(_titles_overlap(a, b) and not _times_compatible(a, b)
               for a, b in combinations(rows, 2))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):      # Windows consoles
        sys.stdout.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "site/public.json")
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    today = jst_today().isoformat()

    groups: dict[tuple, list[dict]] = {}
    for e in events:
        if (e.get("end_date") or e.get("start_date") or "") < today:
            continue
        if not e.get("venue_key") or not e.get("start_date"):
            continue
        groups.setdefault((e["start_date"], e["venue_key"]), []).append(e)

    multi_source = performances = distinct = suspicious = 0
    for (day, vk), rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        srcs = {r["source"] for r in rows}
        if not srcs & PROMOTER_SOURCES:
            continue
        titles = [(r["source"], r.get("start_time") or "--:--",
                   (r.get("title_ja") or r.get("title_en") or "")[:48])
                  for r in rows]
        if len(srcs) > 1:
            multi_source += 1
            print(f"[cross-source] {day} {vk}")
        elif _is_performance_pairing(rows):
            performances += 1       # one show, several performances — ok
            continue
        elif all(not _titles_overlap(a, b)
                 for a, b in combinations(rows, 2)):
            distinct += 1           # different shows, one promoter — ok
            continue
        else:
            # titles align AND times don't disagree — the fold should
            # have taken these; surviving here indicates a merge bug
            suspicious += 1
            print(f"[same-source?] {day} {vk}")
        for src, t, title in titles:
            print(f"    {src:18} {t:5} {title}")

    print(f"\ncross-source groups (promoter involved): {multi_source}")
    print(f"same-source multi-performance days (ok, kept): {performances}")
    print(f"same-source distinct-title days (ok, kept): {distinct}")
    print(f"same-source UNEXPLAINED pairs (fold bug if >0): {suspicious}")


if __name__ == "__main__":
    main()
