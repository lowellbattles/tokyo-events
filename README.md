# Tokyo Events Aggregator

A bilingual (Japanese / English) aggregator for Tokyo events — live music
first, now also art exhibitions, festivals, matsuri, fireworks, and flower
shows. It scrapes venues', promoters', and museums' **official sites**
directly, instead of the third-party aggregators that tend to run stale.
The result is a static, self-updating GitHub Pages site: a daily GitHub
Actions run scrapes, stages everything into a SQLite database committed to
the repo, and republishes.

**Live site:** https://lowellbattles.github.io/tokyo-events/

## Coverage

80 registered sources across roughly 56 scraper modules — many modules
cover several halls sharing one operator's site. Full per-source registry,
live-validation status, and the "checked but not scrapeable" list
(robots.txt disallows, WAF blocks) live in [`CLAUDE.md`](CLAUDE.md).

| Class | Sources | Examples |
|---|---|---|
| Live houses & clubs | 31 | LIQUIDROOM, Shibuya O-East/West/Crest/Nest, Zepp ×4, WWW/WWW X, Club Quattro, duo, Loft/Shelter/Loft Heaven, UNIT, Club Citta', eggman, Billboard Live... |
| Jazz clubs | 2 | Blue Note Tokyo, Cotton Club |
| Concert halls & theaters | 10 | EX THEATER Roppongi, Tokyo International Forum, NHK Hall, Orchard Hall... |
| Arenas, domes & stadiums | 10 | Tokyo Dome, Yokohama Arena, Ariake Arena, K-Arena Yokohama, Kokuritsu Stadium... |
| Promoters (own calendars) | 6 | SOGO TOKYO, CREATIVEMAN, SMASH, UDO ARTISTS, DISK GARAGE, Live Nation Japan |
| Museums & galleries (art) | 17 | Mori Art Museum, Tokyo National Museum, National Art Center, Artizon, Nezu Museum... |
| Curated seasonal (matsuri/hanabi/flowers) | 3 | 11 matsuri + 12 hanabi editions live; flowers grows via a curated watch list |
| Curated festivals | 1 | Fuji Rock, Summer Sonic Tokyo, Rock in Japan, @JAM EXPO... |

Promoters' calendars count as a *primary* source for their own
productions, and the only way to cover "gap venues" with no schedule of
their own (Nippon Budokan and similar) — raw venue strings resolve against
the registry at export; whatever can't be matched is dropped, not guessed.

## Architecture

```
scrapers (per source, grouped into families)
  → SQLite staging (events.db, committed to the repo — "git scraping" pattern)
  → export-time derivation: promoter/venue merge, genre tagging, artist index
  → site/public.json (slim, forward-looking-only feed, ~1.3 MB)
  → static frontend (site/index.html reads ./public.json directly)

GitHub Actions, daily 07:00 JST:
  pytest gate → scrape → export → commit data → deploy to Pages
  on trouble: rolling issues — scraper-error / venue-gap / stale-upcoming
```

- **Two-stage scraping.** A listing pass finds the event inventory cheaply;
  a capped detail pass (40 fetches/source/run) visits new/changed events'
  own pages to fill in times, prices, and ticket links. Content-hash
  change detection keeps a steady-state source down to a few pages a day.
- **Stage, don't publish blind.** Events land `pending` by default; a
  human approves via the CLI, or a run force-publishes everything with
  `AUTO_PUBLISH=true` / `--auto` (how the live deployment runs) — a
  source can also default to `AUTO` in the `pipeline.py` registry once
  it's individually proven reliable.
- **Export-time derivation, not scrape-time.** Venue de-duplication
  (`promoters.py`), genre tagging (`genres.py`), and the artist index
  (`artists.py`) are computed when `cli.py export` runs, from per-source
  facts in SQLite — alias/curation fixes take effect on the next export
  with no re-scraping.
- **JST-anchored, always.** Every "is this today / upcoming" check runs
  off a fixed UTC+9 clock (`textutils.jst_today()`), independent of the
  scraping machine's own timezone — GitHub's runners are UTC.

## Principles

1. **Facts only, link out.** Titles, dates, times, prices, venue, lineup,
   ticket links, source URL — never descriptions or images. That stays on
   the source's own site; we link to it.
2. **Politeness, and no bypassing bot detection.** 2-second-minimum
   per-host rate limits, an identifiable User-Agent, capped detail-fetch
   volume, and a robots.txt check before any source is added. A site
   that disallows us, or whose WAF 403s our honest UA, stays out
   permanently, not "until we find a workaround." We also never scrape
   ticketing companies' own listing pages (e+, Pia) — only official
   sites, promoters' calendars, and a couple of owner-approved
   platforms; `CLAUDE.md` tracks the full checked-and-skipped list.
3. **Structural failure is loud, not silent.** Parsers key off URL
   patterns and text conventions (OPEN/START, ¥ tiers) rather than CSS
   class names, so redesigns rarely break them — a page that's really
   changed shape returns `found=0` or a typed fetch error, not garbage.
4. **Every parser is fixture-tested** against HTML captured from the live
   site (scrubbed of embedded API keys) under `tests/fixtures/`, via
   pure, offline parse functions — `pytest tests/ -q` must stay green.
5. Schema changes move `models.py`, `to_json()`, and `site/index.html`'s
   read side together — the feed contract is one thing, not two things
   that happen to currently agree.

## Usage

```bash
pip install -r requirements.txt
python cli.py scrape                          # all sources -> staging
python cli.py scrape --only zepp_divercity oeast
python cli.py scrape --no-details             # listing pass only, faster
python cli.py scrape --auto                   # publish straight to AUTO status
python cli.py scrape --report run.json        # machine-readable run report
python cli.py list --status pending
python cli.py approve <id> [<id> ...]
python cli.py reject <id> [<id> ...]
python cli.py export site/public.json         # feed for the frontend

python -m pytest tests/ -q                    # offline, fixture-based
python scripts/find_dupes.py site/public.json # duplicate-event report
```

## Adding a source

1. Check the target's robots.txt — if it disallows us, or its WAF 403s our
   honest UA, that's the end of it, no workarounds.
2. `python cli.py scrape --only <source> --no-details --report r.json`
3. `python cli.py list --status pending` — spot-check ~5 events against
   the venue's own page (title, date, times, price, sold-out flag).
4. If `found=0` or fields look wrong: save the raw listing HTML into
   `tests/fixtures/<source>_live.html` (UTF-8), write/adjust tests against
   it, fix the parser, re-run `pytest`, re-scrape.
5. Drop `--no-details` and validate the detail pass: ticket links
   populate, prices don't pick up merch or drink-charge amounts.
6. Once a source runs clean for a few daily cycles, consider giving it
   `ReviewStatus.AUTO` in `pipeline.py`'s registry.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and hard rules, and
[`docs/venue-coverage-roadmap.md`](docs/venue-coverage-roadmap.md) for
per-family expansion notes and venues already ruled out.

## Deployment & automation

[`.github/workflows/scrape-and-deploy.yml`](.github/workflows/scrape-and-deploy.yml)
runs on a **schedule** (22:00 UTC daily = 07:00 JST, plus a manual "Run
workflow" button) and on **push to `main`** touching `site/`, `src/`,
`cli.py`, or the workflow itself (redeploys without re-scraping).

A scheduled run: tests → scrape → export → commit `events.db` +
`site/public.json` → deploy to Pages. The commit step
(`scripts/commit_data.sh`) rebuilds on the remote's current tip rather
than rebasing, so a push landing mid-scrape can't binary-conflict on
`events.db`. `scripts/report_errors.py` then files or updates up to three
rolling issues — `scraper-error`, `venue-gap` (unresolved promoter
venues), `stale-upcoming` (possible cancellations) — without ever failing
the workflow, so one broken source never blocks deploying the rest.

- **`AUTO_PUBLISH`** repo variable (`true`/`false`) — true runs the scrape
  with `--auto` so new events publish immediately instead of waiting for
  review. Currently `true` on the live deployment.
- **`ANTHROPIC_API_KEY`** (optional secret) — enables cached LLM genre
  refinement at export (`genres.py`); without it, rule-based and
  venue-prior tagging still run.

One-time fork setup: repo **Settings → Pages → Source: GitHub Actions**,
and **Settings → Actions → General → Workflow permissions: Read and
write permissions**.

## Docs

- [`CLAUDE.md`](CLAUDE.md) — architecture contract, hard rules, the full
  80-source registry, environment notes.
- [`docs/architecture-and-roadmap.md`](docs/architecture-and-roadmap.md) —
  as-built architecture audit and the current engineering roadmap.
- [`docs/venue-coverage-roadmap.md`](docs/venue-coverage-roadmap.md) —
  per-venue-family source expansion notes.
