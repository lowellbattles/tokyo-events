#!/usr/bin/env python3
"""File (or update) a GitHub issue when any scraper errored.

Reads the run report written by `cli.py scrape --report`, and uses the
`gh` CLI (preinstalled on GitHub runners; GH_TOKEN/GH_REPO from env).

Behavior:
- No errors -> close the rolling issue if one is open, exit 0.
- Errors -> if an open issue labeled 'scraper-error' exists, add a comment;
  otherwise create one. One rolling issue instead of daily spam.
- Never fails the workflow (exit 0 always) — a broken scraper shouldn't
  block deploying the sources that worked.
"""

import json
import subprocess
import sys
from datetime import date

LABEL = "scraper-error"


def gh(*args, capture=False):
    try:
        r = subprocess.run(["gh", *args], capture_output=capture, text=True)
        return r.stdout if capture else None
    except FileNotFoundError:
        print("gh CLI not available; skipping issue filing")
        sys.exit(0)


def find_open_issue() -> str | None:
    raw = gh("issue", "list", "--label", LABEL, "--state", "open",
             "--json", "number", "--limit", "1", capture=True) or "[]"
    try:
        issues = json.loads(raw)
    except json.JSONDecodeError:
        issues = []
    return str(issues[0]["number"]) if issues else None


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    try:
        reports = json.load(open(sys.argv[1]))
    except (OSError, json.JSONDecodeError):
        sys.exit(0)

    failed = [r for r in reports if r.get("error")]
    if not failed:
        print("all scrapers OK")
        num = find_open_issue()
        if num:
            gh("issue", "close", num, "--comment",
               f"All sources OK on {date.today().isoformat()} — closing.")
            print(f"closed issue #{num}")
        sys.exit(0)

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
    sys.exit(0)


if __name__ == "__main__":
    main()
