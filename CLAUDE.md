# CLAUDE.md — Tokyo Events Aggregator

Bilingual (JA/EN) aggregator for Tokyo live-music events (later: art,
matsuri, fireworks, flowers, festivals). Scrapes venue **official sites**
(accurate) instead of third-party aggregators (stale). Static site on
GitHub Pages, self-updating via GitHub Actions.

## Architecture (do not silently change)

```
scrapers (per source, grouped into families)
  → SQLite staging (events.db, committed to repo — "git scraping" pattern)
  → human review OR auto-publish     (cli.py approve / AUTO_PUBLISH var)
  → site/public.json export          (cli.py export)
  → static frontend (site/index.html reads ./public.json)
GitHub Actions: daily 07:00 JST scrape → commit data → deploy Pages
                → files rolling `scraper-error` issue on failures
```

- `src/tokyo_events/models.py` — canonical `Event` dataclass. Bilingual
  title fields, `genres[]`, `ticket_links[]`, `end_date` for multi-day.
- `src/tokyo_events/db.py` — EventStore: upsert with content-hash change
  detection (changed events re-stage as pending), source_health, export.
  Artist tables exist but are unpopulated (future phase).
- `src/tokyo_events/scrapers/` — one module per family. `base.py` has
  polite fetch (rate limit, UA, retries) + generic `parse_detail()`
  enrichment (OPEN/START, ¥ tiers, playguide links, P/L codes).
  `textutils.py` holds shared JP-venue parsing conventions.
- `src/tokyo_events/pipeline.py` — SCRAPERS registry; two-stage scrape
  (listing pass, then detail fetch for new/changed events, capped at 25/run).
- `cli.py` — scrape / list / approve / reject / export, `--auto`, `--report`.

## Registered sources (13)

| Family | source_ids | Status |
|---|---|---|
| Liquidroom | liquidroom | parser from near-raw structure; NOT yet live-validated |
| O-Group | oeast owest ocrest onest | built from rendered capture; NOT yet live-validated |
| Zepp | zepp_divercity zepp_haneda zepp_shinjuku zepp_yokohama | built from rendered capture; NOT yet live-validated |
| Billboard | billboard_tokyo billboard_yokohama | built from live HTML fetch (highest confidence) |
| Pia | toyosu_pit pia_arena_mm | built from rendered capture; NOT yet live-validated |

"Rendered capture" = parser written against text-rendered pages, not raw
HTML. Expect some field-extraction bugs on first live runs. Zepp month
pagination is a known TODO (currently only fetches the default schedule
page; the site holds ~12 months).

## Hard rules

1. **Facts only.** Store titles, dates, times, prices, venue, lineup,
   ticket links, source URL. NEVER copy event descriptions or images from
   source sites — link out. This is a legal + relationship principle.
2. **Politeness.** Keep `rate_limit_s >= 2`, identifiable User-Agent,
   detail-fetch caps. Check robots.txt before adding any new source.
   Never scrape ticketing companies' own aggregation pages (e+ live house
   listings etc.) — official venue sites only.
3. **Parsers key off URL patterns and text conventions (OPEN/START/¥),
   not CSS class names.** Structural failure must be loud (found=0), not
   silent garbage.
4. **Fixture-based tests.** Every parser change needs a fixture under
   `tests/fixtures/` (raw HTML saved from the live site, UTF-8!) and
   passing tests. Parse steps are pure functions — iterate offline.
   `python -m pytest tests/ -q` must stay green before any commit.
5. **Schema changes** to Event/DB: update `models.py`, keep `to_json()`
   in sync with what `site/index.html` reads, and note the change —
   the frontend and feed contract move together.

## Windows environment notes

- Owner's machine is Windows; use PowerShell-compatible commands.
- Japanese text everywhere: if console output garbles, set
  `$env:PYTHONUTF8 = "1"`. Always write/read fixtures as UTF-8
  (`open(..., encoding="utf-8")` explicitly when touching files).
- `py` and `python` both work; prefer `python`.

## Validation workflow (per source)

1. `python cli.py scrape --only <source> --no-details --report r.json`
2. `python cli.py list --status pending` — spot-check 5 events against
   the venue site (title, date, times, price, sold-out).
3. If found=0 or fields are wrong: save the raw listing HTML into
   `tests/fixtures/<source>_live.html`, write/adjust tests against it,
   fix the parser, re-run pytest, then re-scrape.
4. Then validate the detail pass (drop `--no-details`) — confirm
   ticket_links populate and prices don't pick up merch.
5. When a source survives a few days of daily runs cleanly, consider
   promoting it to ReviewStatus.AUTO in the pipeline registry.

## Roadmap priorities (owner-confirmed order)

1. Live-validate all 13 sources (see above).
2. Artist cross-referencing: populate artists/aliases/event_artists from
   `lineup` + solo titles; normalization (NFKC, casing, JA/EN aliases);
   artist pages on the frontend ("this artist's other upcoming shows").
   This is the product's killer feature.
3. Next venue families: Club Quattro, WWW/WWW X, duo, Loft group
   (Shinjuku LOFT / 下北沢SHELTER / Que). Then halls/arenas per
   `docs/venue-coverage-roadmap.md`.
4. LLM-assisted genre tagging in the pipeline (facets in models.GENRES).
5. Festivals as a curated source class (Fuji Rock, Summer Sonic, ...).
6. Later: dedupe across sources, iCal export, OGP/sitemap, custom domain.
