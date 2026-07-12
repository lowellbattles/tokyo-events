#!/usr/bin/env python3
"""Tokyo Events — admin CLI.

Usage:
  python cli.py scrape [--only zepp_divercity oeast] [--no-details]
  python cli.py list [--status pending] [--category music]
  python cli.py approve <event_id> [<event_id> ...]
  python cli.py reject <event_id> [...]
  python cli.py export public_events.json
"""

import argparse
import sys

sys.path.insert(0, "src")

from tokyo_events.db import EventStore           # noqa: E402
from tokyo_events.models import ReviewStatus     # noqa: E402
from tokyo_events import pipeline                # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="events.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape")
    s.add_argument("--only", nargs="*")
    s.add_argument("--no-details", action="store_true")
    s.add_argument("--auto", action="store_true",
                   help="publish without review (AUTO status)")
    s.add_argument("--report", metavar="FILE",
                   help="write machine-readable run report JSON")

    l = sub.add_parser("list")
    l.add_argument("--status")
    l.add_argument("--category")
    l.add_argument("--from", dest="date_from")
    l.add_argument("--to", dest="date_to")

    a = sub.add_parser("approve"); a.add_argument("ids", nargs="+")
    r = sub.add_parser("reject");  r.add_argument("ids", nargs="+")
    e = sub.add_parser("export");  e.add_argument("path")

    args = p.parse_args()
    store = EventStore(args.db)

    if args.cmd == "scrape":
        reports = pipeline.run(
            store, only=args.only, fetch_details=not args.no_details,
            force_status=ReviewStatus.AUTO if args.auto else None)
        if args.report:
            import json
            with open(args.report, "w") as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)
        for rep in reports:
            status = f"ERROR: {rep['error']}" if rep["error"] else "ok"
            print(f"[{rep['source']:>18}] found={rep['found']:>3} "
                  f"new={rep['new']:>3} changed={rep['changed']:>3} "
                  f"details={rep['details']:>3} — {status}")
    elif args.cmd == "list":
        for ev in store.list_events(args.status, args.category,
                                    args.date_from, args.date_to):
            sold = " [SOLD OUT]" if ev.get("is_sold_out") else ""
            print(f"{ev['id']}  {ev['start_date']}  [{ev['status']:>8}] "
                  f"{ev.get('title_ja') or ev.get('title_en')}"
                  f" @ {ev.get('venue_name')}{sold}")
    elif args.cmd in ("approve", "reject"):
        status = (ReviewStatus.APPROVED if args.cmd == "approve"
                  else ReviewStatus.REJECTED)
        for eid in args.ids:
            store.set_status(eid, status)
        print(f"{args.cmd}: {len(args.ids)} event(s)")
    elif args.cmd == "export":
        n = store.export_public_json(args.path)
        print(f"exported {n} public events -> {args.path}")


if __name__ == "__main__":
    main()
