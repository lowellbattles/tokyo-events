# Tokyo Events Aggregator

A bilingual (JA/EN) aggregator for Tokyo events, starting with live music.
Pulls from the accurate sources (venue websites) rather than lagging
third-party aggregators.

## Current coverage — 13 sources, 5 scraper families

| Family | Sources | Scraper |
|---|---|---|
| LIQUIDROOM | liquidroom (Ebisu) | `scrapers/liquidroom.py` |
| Shibuya O-Group | oeast, owest, ocrest, onest | `scrapers/ogroup.py` |
| Zepp chain | zepp_divercity, zepp_haneda, zepp_shinjuku, zepp_yokohama | `scrapers/zepp.py` |
| Billboard Live | billboard_tokyo, billboard_yokohama | `scrapers/billboard.py` |
| Pia venues | toyosu_pit, pia_arena_mm | `scrapers/pia.py` |

Adding a hall within an existing family = one registry line in
`pipeline.py`. See `docs/venue-coverage-roadmap.md` for the full rollout plan.

## Architecture

**Two-stage scraping.** Listing pages give the event inventory cheaply;
the pipeline then fetches each NEW or CHANGED event's own page once
(`parse_detail`) to fill missing times/prices and collect **ticket links**
(e+/Pia/Lawson URLs plus Pコード/Lコード), capped per run for politeness.
After initial backfill this is only a handful of requests per venue per day
thanks to content-hash change detection.

**Scrape → stage → review → publish.** Everything lands as `pending`;
humans approve via CLI (web admin later). Changed events are automatically
re-staged. Trusted sources can be promoted to AUTO in the registry.
A scraper returning zero events is flagged as a probable site redesign.

**Facts only.** Titles, dates, times, prices, venue, lineup, source URL,
ticket links. Descriptions and images stay at the source; we link out.

**Schema highlights** (`models.py`): bilingual title fields, `genres[]`
facets (idol / j-rock / international / k-pop / jazz-soul / classical /
hiphop-rnb / electronic / anime-seiyu), `ticket_links[]`, multi-day
`end_date`, and artist tables ready in SQLite for the cross-referencing
phase.

## Usage

```bash
pip install -r requirements.txt
python cli.py scrape                        # all sources -> staging
python cli.py scrape --only zepp_divercity oeast
python cli.py scrape --no-details           # listing pass only
python cli.py list --status pending
python cli.py approve <id> [<id>...]
python cli.py export public.json            # feed for the frontend
python -m pytest tests/                     # offline, fixture-based
```

## ⚠ First-run validation required

The Liquidroom parser was built against near-raw page structure; the Zepp
and O-Group parsers were built from **rendered-text captures**
(2026-07-02) and the Billboard parser from a live HTML fetch. On your
first real run per source:

1. `python cli.py scrape --only <source> --no-details`
2. If `found=0` or fields look wrong, save the raw listing HTML into
   `tests/fixtures/` and adjust that scraper's block-walking/regexes —
   the parse step is pure and fixture-tested, so iterate offline.
3. Spot-check 5 events against the venue site before approving.

All parsers deliberately key off URL patterns and text conventions
(OPEN/START/¥) rather than CSS class names, so theme tweaks rarely break
them — and structural changes fail loudly as `found=0`.

## Automation & deployment (GitHub Pages)

The repo is a self-updating site. `.github/workflows/scrape-and-deploy.yml`:

- **07:00 JST daily** (+ manual "Run workflow" button): runs tests, scrapes
  all 13 sources, commits `events.db` + `site/public.json` back to the repo,
  and deploys `site/` to GitHub Pages.
- **On push to main** (site/src changes): redeploys without scraping.
- **On scraper failure**: files/updates a rolling GitHub issue labeled
  `scraper-error` with the traceback (`scripts/report_errors.py`). A broken
  source never blocks deploying the ones that worked.

One-time setup after pushing to GitHub:
1. Repo **Settings → Pages → Source: "GitHub Actions"**.
2. **Settings → Actions → General → Workflow permissions:
   "Read and write permissions"** (the bot commits data).
3. Optional repo **variable** `AUTO_PUBLISH=true` to skip human review and
   publish scrapes directly (recommended only after a source has proven
   reliable; default keeps the pending→approve flow, in which you approve
   locally via `python cli.py approve` and push).
4. Run the workflow once manually; the site goes live at your Pages URL.

`site/` ships with a fixture-built demo feed
(`python scripts/build_demo_feed.py`) so the page renders before the first
real scrape. The frontend (`site/index.html`) reads `./public.json`,
renders the JA/EN UI, genre/venue/date filters, ticket-provider badges
from `ticket_links`, and a source-health footer from the feed's
`sources` block.

## Roadmap

1. Live-validate the 13 sources; promote stable ones to AUTO.
2. Next families: Club Quattro, WWW, Loft group.
3. Artist entity population from lineups -> artist pages (cross-referencing).
4. LLM-assisted genre tagging in the review step.
5. Festivals (Fuji Rock, Summer Sonic, ...) as a curated source class.
6. Custom domain + OGP/sitemap for search discoverability.
