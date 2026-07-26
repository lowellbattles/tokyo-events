#!/usr/bin/env python3
"""File (or update) GitHub issues from the scrape run report.

Reads the run report written by `cli.py scrape --report`, and uses the
`gh` CLI (preinstalled on GitHub runners; GH_TOKEN/GH_REPO from env).

Behavior:
- Errors -> one rolling issue labeled 'scraper-error': comment if open,
  create otherwise. No errors -> close it if open.
- Unresolved venue strings (promoter sources' skipped_venues) -> one
  rolling issue labeled 'venue-gap' whose BODY is edited in place to the
  current gap list (edits don't notify, so a persistent gap costs one
  issue, not daily spam). No gaps -> close it.
- Never fails the workflow (exit 0 always) — a broken scraper shouldn't
  block deploying the sources that worked.
"""

import json
import subprocess
import sys
from datetime import date

LABEL = "scraper-error"
GAP_LABEL = "venue-gap"


def gh(*args, capture=False):
    try:
        r = subprocess.run(["gh", *args], capture_output=capture, text=True)
        return r.stdout if capture else None
    except FileNotFoundError:
        print("gh CLI not available; skipping issue filing")
        sys.exit(0)


def find_open_issue(label: str = LABEL) -> str | None:
    raw = gh("issue", "list", "--label", label, "--state", "open",
             "--json", "number", "--limit", "1", capture=True) or "[]"
    try:
        issues = json.loads(raw)
    except json.JSONDecodeError:
        issues = []
    return str(issues[0]["number"]) if issues else None


def handle_errors(reports: list[dict]) -> None:
    failed = [r for r in reports if r.get("error")]
    if not failed:
        print("all scrapers OK")
        num = find_open_issue()
        if num:
            gh("issue", "close", num, "--comment",
               f"All sources OK on {date.today().isoformat()} — closing.")
            print(f"closed issue #{num}")
        return

    lines = [f"Scrape run {date.today().isoformat()} — "
             f"{len(failed)}/{len(reports)} source(s) errored:", ""]
    for r in failed:
        lines.append(f"### `{r['source']}`")
        lines.append(f"found={r['found']} new={r['new']} "
                     f"changed={r['changed']}")
        lines.append("```")
        lines.append(str(r["error"]).strip()[:1500])
        lines.append("```")
        lines.append("")
    lines.append("_Likely a site-structure change. Save the raw listing "
                  "HTML into `tests/fixtures/` and adjust the parser "
                  "(see README: First-run validation)._")
    body = "\n".join(lines)

    # ensure label exists (idempotent)
    gh("label", "create", LABEL, "--color", "d73a4a",
       "--description", "A venue scraper is failing", "--force")

    num = find_open_issue()
    if num:
        gh("issue", "comment", num, "--body", body)
        print(f"commented on existing issue #{num}")
    else:
        title = f"Scraper errors: {', '.join(r['source'] for r in failed)}"
        gh("issue", "create", "--title", title, "--body", body,
           "--label", LABEL)
        print("created new issue")


def build_gap_body(reports: list[dict]) -> str | None:
    """Markdown body for the rolling venue-gap issue, or None if no gaps."""
    gaps = {r["source"]: sorted(set(r.get("skipped_venues") or []))
            for r in reports}
    gaps = {s: v for s, v in gaps.items() if v}
    if not gaps:
        return None
    total = sum(len(v) for v in gaps.values())
    lines = [f"Venue strings promoter scrapers could not resolve to "
             f"`venues.CANONICAL` (as of {date.today().isoformat()}) — "
             f"{total} across {len(gaps)} source(s).",
             "",
             "Events at these venues are being DROPPED. Kanto venues here "
             "are curation gaps: add the hall (or an alias) to "
             "`src/tokyo_events/venues.py`. Non-Kanto strings are usually "
             "fine to ignore.",
             ""]
    for source in sorted(gaps):
        lines.append(f"### `{source}`")
        lines.extend(f"- {v}" for v in gaps[source])
        lines.append("")
    lines.append("_This body is rewritten by each daily run "
                 "(`scripts/report_errors.py`); it always reflects the "
                 "latest scrape._")
    return "\n".join(lines)


def handle_gaps(reports: list[dict]) -> None:
    body = build_gap_body(reports)
    num = find_open_issue(GAP_LABEL)
    if body is None:
        print("no venue gaps")
        if num:
            gh("issue", "close", num, "--comment",
               f"No unresolved venues on {date.today().isoformat()} — "
               "closing.")
            print(f"closed gap issue #{num}")
        return

    gh("label", "create", GAP_LABEL, "--color", "fbca04",
       "--description", "Promoter events dropped: venue not in registry",
       "--force")
    if num:
        # edit in place: idempotent, and body edits don't send notifications
        gh("issue", "edit", num, "--body", body)
        print(f"updated gap issue #{num}")
    else:
        gh("issue", "create", "--title",
           "Venue curation gaps (unresolved promoter venues)",
           "--body", body, "--label", GAP_LABEL)
        print("created gap issue")


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    try:
        reports = json.load(open(sys.argv[1]))
    except (OSError, json.JSONDecodeError):
        sys.exit(0)

    handle_errors(reports)
    handle_gaps(reports)
    sys.exit(0)


if __name__ == "__main__":
    main()
