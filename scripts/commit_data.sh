#!/usr/bin/env bash
# Commit the freshly scraped data files back to main without ever losing
# a day's data to a rebase conflict (roadmap R1 / OPS-1).
#
# events.db and site/public.json are pure OUTPUTS of this run — by
# construction newer than whatever the remote holds. So we never rebase
# them (a binary conflict on events.db killed the 2026-07-26 run when a
# manual push landed mid-scrape). Instead we rebuild the data commit on
# top of the current remote tip:
#
#   stash fresh files -> fetch -> hard-reset to origin/main -> restore
#   fresh files -> commit -> push, retrying the whole cycle on a pure
#   push race (someone pushed between our fetch and our push).
#
# Untracked files (scrape_report.json) survive the hard reset, so the
# error-reporting step still sees the report. If all attempts fail the
# step exits 1 — visible as a red run — but the workflow's later steps
# are guarded with !cancelled() so error reporting still happens, and
# the next daily run regenerates and commits the data anyway.
#
# Assumes: git identity already configured, cwd = repo root.
# Env overrides (used by tests/simulation): COMMIT_RETRIES,
# COMMIT_RETRY_SLEEP, COMMIT_REMOTE, COMMIT_BRANCH.

set -euo pipefail

FILES=("events.db" "site/public.json")
RETRIES="${COMMIT_RETRIES:-3}"
SLEEP_BASE="${COMMIT_RETRY_SLEEP:-5}"
REMOTE="${COMMIT_REMOTE:-origin}"
BRANCH="${COMMIT_BRANCH:-main}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in "${FILES[@]}"; do
  mkdir -p "$TMP/$(dirname "$f")"
  cp "$f" "$TMP/$f"
done

for attempt in $(seq 1 "$RETRIES"); do
  git fetch "$REMOTE" "$BRANCH"
  git reset --hard "$REMOTE/$BRANCH"
  for f in "${FILES[@]}"; do
    cp "$TMP/$f" "$f"
  done
  git add "${FILES[@]}"
  if git diff --cached --quiet; then
    echo "no data changes to commit"
    exit 0
  fi
  git commit -m "data: daily scrape $(date -u +%F)"
  if git push "$REMOTE" "HEAD:$BRANCH"; then
    echo "data commit pushed (attempt $attempt)"
    exit 0
  fi
  echo "push rejected ($BRANCH moved during the scrape) — retrying"
  sleep $((attempt * SLEEP_BASE))
done

echo "::error::data commit could not be pushed after $RETRIES attempts"
exit 1
