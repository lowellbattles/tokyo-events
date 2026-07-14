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
  `category` is "music" for concerts; mixed arena/hall calendars mark
  sports/ice shows/ceremonies as "other" (`textutils.is_nonmusic`), and
  the frontend main list shows only "music".
- `src/tokyo_events/db.py` — EventStore: upsert with content-hash change
  detection (changed events re-stage as pending), source_health, export.
  Upsert merges stored detail-pass fields into listing events that lack
  them (listing gaps are neither changes nor destructive).
- `src/tokyo_events/artists.py` — artist index rebuilt at export from
  lineups + guarded title matching; fills artists/artist_aliases/
  event_artists and each exported event's `artists[]`.
- `src/tokyo_events/venues.py` — canonical venue registry (normalized
  aliases; scraped source_ids + promoter-only gap venues like Budokan,
  each with a vclass: livehouse/jazz/hall/arena the frontend filters by).
- `src/tokyo_events/promoters.py` — export-time merge for promoter
  sources: duplicate rows fold into venue records (sold-out OR,
  ticket-link union, gap-fill); gap-venue events export standalone under
  `venue_key`, which the frontend uses as venue identity
  (`venue_key || source`).
- `src/tokyo_events/genres.py` — export-time tagging: rules → cached LLM
  (optional) → `_VENUE_PRIOR` venue defaults.
- `src/tokyo_events/scrapers/` — one module per family. `base.py` has
  polite fetch (rate limit, UA, retries, declared-charset-first decoding)
  + generic `parse_detail()` enrichment (OPEN/START, ¥ tiers, playguide
  links, P/L codes). `textutils.py` holds shared JP-venue parsing
  conventions (incl. `add_months` for month-page walks).
- `src/tokyo_events/pipeline.py` — SCRAPERS registry; two-stage scrape
  (listing pass, then detail fetches for new/changed events plus the
  stored missing-details backlog, capped at DETAIL_CAP=40/source/run).
- `cli.py` — scrape / list / approve / reject / export, `--auto`, `--report`.

## Registered sources (55, all live-validated with fixture tests)

| Family / class | source_ids | Notes |
|---|---|---|
| Liquidroom | liquidroom | |
| O-Group | oeast owest ocrest onest | |
| Zepp | zepp_divercity zepp_haneda zepp_shinjuku zepp_yokohama | month pages `?_y=YYYY&_m=M`, walks 6 months |
| Billboard | billboard_tokyo billboard_yokohama | scraper-set genres |
| Pia | toyosu_pit pia_arena_mm | |
| Shibuya indie (step 3) | quattro_shibuya www www_x duo | |
| Loft group | loft_shinjuku shelter loft_heaven | LOFT9 excluded (talk venue) |
| Live houses (2026-07-13) | unit_daikanyama club_citta eggman shibuya_dive reny_shinjuku que_shimokitazawa yokohama_bay_hall fever_shindaita veats_shibuya club_seata stellar_ball | reny = ruido.org (detail-page-driven, RUIDO family expandable); que = clubque.net (operator changed 2026-07); stellar_ball under princehotels.co.jp |
| Jazz (Blue Note Japan) | bluenote_tokyo cotton_club | jazz-soul prior |
| Halls / theaters | ex_theater line_cube_shibuya hulic_hall kanadevia_hall sgc_hall_ariake tokyo_intl_forum nhk_hall opera_city tachikawa_stage_garden orchard_hall | ex_theater + sgc_hall = TV-Asahi TDP JSON feeds; tokyo_intl_forum funnels the 8-hall complex to Hall A concerts via the detail pass; hulic = hulic-theater.com |
| Arenas / domes / stadiums | yokohama_arena tokyo_dome tokyo_garden_theater ariake_arena toyota_arena_tokyo k_arena_yokohama yoyogi_gym1 kokuritsu_stadium makuhari_messe yokohama_buntai | tokyo_dome = one static full-year page, concert rows only; makuhari uses the site's own music-category filter (?c=2); kokuritsu = jns-e.com (MUFG naming) |
| Promoters (2026-07-14) | sogo_tokyo creativeman | promoters' own calendars — a PRIMARY source for their productions; covers gap venues (Budokan, Kinema Club, 東京体育館, ZOZO Marine...) and carries sold-out badges; venue strings stored RAW, resolved + deduped against venue sources at export (venues.py + promoters.py); unresolved venue strings are skipped — extend venues.CANONICAL to admit new halls |

Checked and NOT scrapeable (2026-07-13): Budokan (official site
publishes no concert listings), Hibiya Yaon (closed for reconstruction),
Koenji HIGH + Tokyo Taiikukan (robots.txt disallow), Pacifico Yokohama
(no public schedule), Tokyo Kinema Club (kinema.tokyo calendar empty —
revisit). Future family leads: RUIDO group (Akabane/Yokohama ReNY...),
SALOON (saloon-tokyo.com, UNIT's sister floor), other TDP JSON feeds.

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
   **Scrub captured HTML before committing**: venue pages embed their
   own API keys (Google Maps etc.) — grep fixtures for `AIza`/token
   patterns and replace with `...-REDACTED` (GitHub secret scanning
   flags them otherwise; happened 2026-07-12 with www_schedule_live).
5. **Schema changes** to Event/DB: update `models.py`, keep `to_json()`
   in sync with what `site/index.html` reads, and note the change —
   the frontend and feed contract move together.

## Windows environment notes

- Owner's machine is Windows; use PowerShell-compatible commands.
- Japanese text everywhere: if console output garbles, set
  `$env:PYTHONUTF8 = "1"`. Always write/read fixtures as UTF-8
  (`open(..., encoding="utf-8")` explicitly when touching files).
- In Claude Code's shell, `python` resolves to the Microsoft Store stub
  and `py` is missing — use the full path:
  `$env:LOCALAPPDATA\Programs\Python\Python312\python.exe` (3.12.10).
  (In the owner's own terminals, plain `python` works.)
- gh CLI: `$env:ProgramFiles\GitHub CLI\gh.exe` (new shells have it on
  PATH). Repo: lowellbattles/tokyo-events, Pages at
  https://lowellbattles.github.io/tokyo-events/, AUTO_PUBLISH=true.
- Optional ANTHROPIC_API_KEY repo secret enables LLM genre refinement
  (genres.py); without it, rule-based tagging runs at export.

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

1. ~~Live-validate all sources~~ DONE 2026-07-13 (53 sources).
2. ~~Artist cross-referencing~~ DONE 2026-07-13 (artists.py at export;
   frontend artist pages match the canonical `artists[]` field). Still
   open within it: human/LLM alias merging across JA/EN spellings.
3. ~~Venue build-out (live houses, halls, arenas)~~ DONE 2026-07-13 —
   see the source table above; leads for later: RUIDO family, SALOON,
   more TDP feeds. `docs/venue-coverage-roadmap.md` has per-venue notes.
4. LLM-assisted genre tagging in the pipeline (facets in models.GENRES) —
   rule+prior+cached-LLM tagging exists at export; extend as needed.
5. Festivals as a curated source class (Fuji Rock, Summer Sonic, ...).
6. Later: dedupe across sources (venue aliases: Kanadevia Hall ex-TDC
   Hall, MUFG Stadium ex-国立競技場), iCal export, OGP/sitemap, custom
   domain. New-source AUTO promotion after a few clean daily runs.
