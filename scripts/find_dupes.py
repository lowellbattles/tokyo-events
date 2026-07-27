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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokyo_events.promoters import PROMOTER_SOURCES  # noqa: E402
from tokyo_events.scrapers.textutils import jst_today  # noqa: E402


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

    multi_source = same_source = 0
    for (day, vk), rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        srcs = {r["source"] for r in rows}
        titles = [(r["source"], (r.get("title_ja") or r.get("title_en")
                                 or "")[:48]) for r in rows]
        if len(srcs) > 1 and srcs & PROMOTER_SOURCES:
            multi_source += 1
            print(f"[cross-source] {day} {vk}")
        elif len(srcs) == 1 and len(rows) > 1 and srcs & PROMOTER_SOURCES:
            same_source += 1
            print(f"[same-source]  {day} {vk}")
        else:
            continue
        for src, title in titles:
            print(f"    {src:18} {title}")

    print(f"\ncross-source groups (promoter involved): {multi_source}")
    print(f"same-source promoter double-rows: {same_source} (R6 scope)")


if __name__ == "__main__":
    main()
