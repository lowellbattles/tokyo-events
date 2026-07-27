# Architecture audit & engineering roadmap

Audited **2026-07-27** against commit `d9e537f` (all 540 tests green). This
document maps the system as actually built, records every bug/risk found
with evidence, and lays out fixes and adds as discrete items that can be
picked up **one at a time** — each item is one PR-sized unit with
acceptance criteria. Companion docs: `docs/venue-coverage-roadmap.md`
(per-venue source expansion), `CLAUDE.md` (hard rules + source registry).

Numbers below were computed from the working tree's `events.db` /
`site/public.json` on the audit date; re-derive them before relying on
exact counts later (the daily cron moves them).

---

## 1. Architecture as built (verified against code)

```
                    ┌──────────────────────────────────────────────┐
                    │ 56 scraper modules (13.5k LOC) → 82 sources  │
                    │ scrapers/base.py: polite fetch (2s+, honest  │
                    │ UA, retries, declared-charset-first decode)  │
                    │ scrapers/textutils.py: JP-venue conventions  │
                    │ (OPEN/START, ¥ tiers, 完売, year inference)  │
                    └──────────────┬───────────────────────────────┘
                                   │ Event (models.py, facts only)
                                   ▼
   pipeline.run() ─ per source: listing parse → upsert → detail pass
   │  identity   = sha256(source|source_url)        [dedupe_key]
   │  change     = sha256(all fields)               [content_hash]
   │  detail cap = 40/source/run: new/changed → backlog drain
   │               (never-enriched, upcoming) → sold-out sweep
   │               (≤10 days out, not yet sold out)
   ▼
   EventStore (db.py, SQLite events.db — committed to git)
   │  status machine: pending → approved/rejected (human, cli.py)
   │                  AUTO default via cli --auto (CI AUTO_PUBLISH=true)
   │  changed events: re-stage to pending UNLESS rejected (sticky)
   │                  or default AUTO (keeps current status)
   │  upsert merges stored detail fields into barer listing rows
   ▼
   export (cli.py export → db.export_public_json)     [EXPORT-TIME ONLY]
   │  1. promoters.apply_promoter_merge — venue_key on every event;
   │     promoter rows folded into venue rows (same date+venue+artist
   │     overlap); festival-duplicate folding; unresolved venues dropped
   │  2. genres.apply_genres — scraper-set → cached LLM → rules → prior
   │  3. artists.apply_artists — rebuild artist tables from lineups +
   │     guarded title matching; set events[].artists[]
   ▼
   site/public.json {generated_at, sources[], events[]}  (3.58 MB today)
   ▼
   site/index.html — single-file frontend, no build step; sections
   music/art/まつり・花火; hash routes #/artist/… #/venue/…; JST-anchored
   "today" (verified correct for any viewer timezone)

   CI (.github/workflows/scrape-and-deploy.yml):
   cron 22:00 UTC (=07:00 JST) → pytest gate → scrape (--auto if
   AUTO_PUBLISH) → export → commit events.db+public.json (pull --rebase)
   → rolling issues (scraper-error / venue-gap) → Pages deploy.
   Push to main (site/src paths) → redeploy without scraping.
```

Key design properties worth preserving (they are pulling their weight):

- **Facts only, link out** — no descriptions/images copied; legal and
  relationship posture.
- **Pure parse steps + fixture tests** — every parser is offline-testable;
  the suite is disciplined (no `len>0`-only assertions anywhere; garbage
  input tests near-universal).
- **Export-time derivation** (venue_key, genres, artists, merges) — the DB
  stays per-source facts; alias/curation updates re-resolve without
  re-scraping. This is the right call and several fixes below lean on it.
- **Loud structural failure** — found=0 errors, rolling GitHub issues,
  `allow_empty` opt-out for seasonal sources.
- **Politeness, audited clean** — no `rate_limit_s < 2` anywhere, detail
  fetches capped (one exception noted in SCR-14), no unbounded loops,
  merch/drink-charge amounts consistently excluded from price minimums,
  encoding handling deliberate (declared charset first).

## 2. Current state in numbers (2026-07-27)

| Metric | Value |
|---|---|
| Registered sources | 82 across 56 modules (75/82 have per-hall fixture tests) |
| Tests | 540 passing, ~12 s |
| DB events | 3,404 published (2,539 approved + 865 auto), 0 pending, 0 rejected |
| Feed events | 3,121 exported — **1,019 (32.6%) already past** |
| Feed size | 3.58 MB (28% is `indent=2` whitespace; ~24% never-read fields) |
| Current/future music events | 1,936 |
| — with price | 65% · with start_time 88% · with ticket links 27% |
| — with artists | 52% · with genres 56% · **with title_en ≈ 0%** (32 site-wide, mostly art) |
| Art events current | 68 (43 with admission price / free flag) |
| Sources failing now | 2 — `mot`, `what_museum` (HTTP 403, rolling issue #5) |
| Same-day/venue duplicate groups on site | 26 (cross-source, unmerged) |
| Daily "changed" churn | ~150 events/day (some legit, some mechanical — see SCR-2) |
| genre_cache rows | 0 — **the LLM genre pass has never run** (no API key in CI) |
| Daily run duration | ~32 min |
| Repo | 11.6 MB after 63 commits; events.db 5.1 MB + public.json 3.6 MB re-committed daily |

## 3. Risk register — everything found, with evidence

Severity: **P0** breaks operation or actively corrupts what users see ·
**P1** visible quality/correctness gaps · **P2** latent traps and hygiene ·
**P3** strategic.

### 3.1 Operations / CI

| ID | Sev | Finding |
|---|---|---|
| OPS-1 | P0 | **Daily data commit dies on rebase conflict; the whole day is lost silently.** `git pull --rebase` (workflow line 61) hit a binary conflict on `events.db` on 2026-07-26 (run 30193388859) because the owner pushed during the 30-min scrape — the push failed, and because no later step has `if: always()`, the scraper-error issue was never filed **and** the Pages artifact was never uploaded, so the site didn't deploy either. Any out-of-band push during a scrape reproduces this. |
| OPS-2 | P0 | **`mot` + `what_museum` return HTTP 403** (rolling issue #5, since 2026-07-27). Both worked at validation on 07-26; simultaneous onset smells like WAF/datacenter-IP blocking of Actions runners rather than a UA policy change. Needs local-vs-runner triage; hard rule 2 (never bypass bot detection) governs the outcome. |
| OPS-3 | P2 | Push-triggered runs re-export `public.json` (export step lacks the `if: github.event_name != 'push'` guard the scrape step has) and deploy it without committing — deployed artifact and repo diverge (at minimum `generated_at`). Confusing during debugging, not data-incorrect. |
| OPS-4 | P2 | Workflow `permissions:` is workflow-level; the `deploy` job inherits `contents:write` + `issues:write` it never needs. |
| OPS-5 | P2 | `requirements.txt` is floor-pinned only (`requests>=2.31`, …) with no lockfile — an upstream release can change CI behavior with no repo diff. |
| OPS-6 | P2 | actions/checkout@v4 + setup-python@v5 emit Node 20 deprecation warnings; GitHub is forcing Node 24. Bump before they hard-break. |
| OPS-7 | P3 | **Repo growth**: events.db (5.1 MB binary) + public.json re-committed daily. Pack is 7.7 MiB after ~2 weeks of dailies. Fine today; multi-year trajectory is hundreds of MB. Architecture note (git-scraping pattern) says don't change silently — this is a decide-later item, not a fix. |

### 3.2 Export / feed contract

| ID | Sev | Finding |
|---|---|---|
| EXP-1 | P0 | **The feed never prunes.** `export_public_json` (db.py:265-279) exports every approved/auto event ever seen: 1,019 past events (34.6% of `events[]` bytes) ship to every visitor and are discarded client-side by `inWindow()`. Unbounded growth, confirmed monotonic in git history (19 KB → 3.63 MB in 15 days). |
| EXP-2 | P0 | **~24% of `events[]` bytes are fields the frontend never reads** (verified against every field access in index.html): `price_text` (383 KB — the single largest field in the payload), `id`, `status`, `ticket_url`, `lat`, `lng`, `tags`. Plus 88 category-`other` events (sports/ceremonies) that the UI never shows, and `sources[].skipped_venues` — internal curation strings exposed publicly. |
| EXP-3 | P1 | `indent=2` costs 1.06 MB (28%) of the file for zero consumer benefit. |
| EXP-4 | P1 | `generated_at` is a **naive** timestamp (db.py:275). The frontend parses it as viewer-local time then renders it as JST — the "Data updated" footer is wrong for every viewer by their own UTC offset (9 h for JST viewers). Same naive-`datetime.now()` pattern in pipeline.py:212,280 and db.py:112 means `first_seen`/`last_seen` mix owner-local JST and runner-UTC wall clocks in one column. |
| EXP-5 | P2 | Export behavior (include/exclude past, categories) has **zero tests** — underspecified contract, no regression protection for EXP-1/2 fixes. |

### 3.3 Scrape-layer correctness (systemic)

| ID | Sev | Finding |
|---|---|---|
| SCR-1 | P1 | **"Today" is machine-local everywhere in the scrape layer** — 57 `date.today()` call sites across 45 scraper files, plus db.py:203 and the sweep's SQL `date('now')`. On the UTC runner at 07:00 JST, "today" is *yesterday in JST* for the entire run: just-closed exhibitions stay listed a day longer, and on the ~12 days/year when JST-today is the 1st, every `today().replace(day=1)` month-walk starts one month early (Jan 1 compounds: month AND year context wrong for that run). No JST-aware clock exists anywhere in `src/` (grep for zoneinfo/Asia/Tokyo: zero functional hits). The frontend anchors to JST correctly; the backend does not. |
| SCR-2 | P1 | **Sold-out flag oscillation / churn.** `is_sold_out` is not in `DETAIL_FILL_FIELDS` (db.py:81-82): for venues whose SOLD OUT badge exists only on detail pages, every listing pass rewrites the event to `is_sold_out=False` (a "changed" upsert), and the sweep re-marks it `True` (another "changed") — a permanent two-writes-per-day loop that also wastes a detail fetch. Telemetry consistent: ~150 changed/day steady-state; `bluenote_tokyo` alone re-hashes 6–8 events every single run. |
| SCR-3 | P2 | **Never-enriched events are refetched daily forever.** `events_needing_detail` (db.py:191-224) has no attempt memory: an event whose detail page simply lacks parseable fields re-enters the backlog every run. `tokyo_intl_forum` alone contributes 49 such events → ~40 wasted fetches/day on one source (politeness + runtime cost). |
| SCR-4 | P1 | **`fetch()` conflates permanent and transient HTTP errors, and ~30 month-walking scrapers use the resulting bare `RuntimeError` as the normal "walked past the calendar end" signal** (e.g. club_citta.py:108-110, disk_garage.py:157-161, tachikawa_sg.py:121-125, sogo_tokyo.py:100-105, smash.py:135-140). A partial block (month 1 OK, 403/429 from month 2 on) is silently swallowed as "no more months": `found` stays >0, the loud-zero canary never fires, and coverage quietly truncates. This is the codebase's single most cross-cutting silent-failure risk — precisely the failure mode hard rule 3 exists to prevent. Politeness angle: `fetch()` also retries 4xx twice with backoff, so every normal month-walk termination burns 2 pointless extra requests + ~6 s — ~60 wasted requests across every daily run. |
| SCR-5 | P2 | `parse_prices`/`YEN_RE` (textutils.py:19,140-147) only match ¥-prefixed amounts; the very common `前売 3,500円` (円-suffixed, no ¥) yields nothing. Likely a systematic contributor to the 35% of music events without a price. The art-side `parse_admission` handles 円-suffixed correctly — music doesn't. Concrete instance: eggman.py documents its venue's bare `4400yen` / `一般3,400` formats and has listing-side parsing for them, but inherits the generic ¥-only `parse_detail`, so detail-only prices at eggman can never enrich. |
| SCR-6 | P2 | `SOLD_OUT_RE` matched against whole detail-page text (base.py:133) can false-positive on merch sellouts (グッズ完売). Precision-first design elsewhere suggests adding a グッズ/物販-context guard. |

### 3.4 Scrape-layer correctness (per-module, from the 56-module deep-dive)

| ID | Sev | Finding |
|---|---|---|
| SCR-7 | P1 | **`PiaArenaMMScraper.scrape()` (pia.py:134-139) has no try/except around its month-walk** — its own sibling `ToyosuPitScraper` 60 lines up wraps the identical pattern with `except RuntimeError: break` ("a missing far-future page is normal"). The day Pia Arena has fewer than `months_ahead` month pages published, the source errors loudly every run. |
| SCR-8 | P1 | **Row-month never cross-checked against page-month context** in `PiaArenaMMScraper._parse` (pia.py:156-159): `dt.date(month.year, mo, day)` with no `mo == month.month` guard — a spillover/adjacent-month row (documented as common in unit.py's calendar-grid notes) silently gets the wrong year near Dec/Jan. Six other scrapers guard this exact case (line_cube.py:186-202, tachikawa_sg.py:212-231, unit.py:214-232, garden_theater.py:228-246, stellar_ball.py:152-162, yokohama_buntai.py:214-235); hulic_hall.py:187-205 and opera_city.py:145-166 share the unguarded shape at lower exposure. |
| SCR-9 | P1 | **The date-range parser exists in three copies, and the one missing a sanity guard is the production fallback.** museums.py:66-84 and museums.py:635-648 are byte-identical (both reject ranges > 3 years); mori.py:76-94 is a third copy **without** that guard — and `parse_any_date_range` (museums.py:91-97) falls back to the mori copy for `yamatane`, `sompo`, `top_museum`, and `opera_city_gallery`. A garbled two-date match on any of those four can silently produce a multi-year "exhibition." |
| SCR-10 | P2 | Dead-wrong fallback duplicated in duo.py:80, quattro.py:103, que.py:202, www.py:111: `tu.infer_year(dt.date.today().month, day, today)` uses the *wall-clock* month as the row's month. Only unreachable today because `scrape()` always passes `month=`; a refactor or direct `parse()` call arms it. |
| SCR-11 | P2 | club_citta.py:128 `soup.find(...) or soup` — the package's one "widen to whole document" fallback instead of loud found=0 when the structural anchor disappears (mitigated by downstream guards, but against house style). |
| SCR-12 | P2 | festivals.py:509-518: one failed lineup fetch discards **all** of an edition's already-fetched payloads (try/except wraps the loop instead of each URL). Self-heals next run; reduces lineup completeness unnecessarily. |
| SCR-13 | P2 | toyota_arena.py:61-79 parses regexes against a doubly-escaped Next.js RSC flight payload — the most fragile technique in the package. Failure is loud (returns []) and the author documented it; flagged for maintainers, no action needed until it breaks. |
| SCR-14 | P2 | udo.py:256-269 fetches **every** show's detail page every run with no self-imposed cap (safe only because UDO lists ~12 shows; creativeman caps at 25, museums have DETAIL_FETCH_CAP). |
| SCR-15 | P2 | **Duplication debt across the scraper package** (each copy drifts independently): ex_theater.py vs sgc_hall.py near-verbatim twins (scrape/parse_detail/regexes identical — ex_theater.py:85-171 vs sgc_hall.py:111-199); the `_richness` keep-richest-block idiom ×3 (liquidroom.py:63-68, loft.py:117-120, ogroup.py:76-80); `MULTI_ACT_RE` ×2 (smash.py:109-111, disk_garage.py:130-132); the "#date fragment when a detail id spans multiple dates" algorithm ×5 (creativeman, disk_garage.py:203-213, smash.py:169-183, sogo_tokyo.py:126-140, tachikawa_sg.py:146-160); label→value block mapping ×7 (sogo_tokyo/disk_garage/tachikawa_sg/sgc_hall/ex_theater/kokuritsu_stadium/nhk_hall). Plus cleanups: base.py:122 shadowing `import re`; base.py:96 missing `raise … from`; galleries.py:180 function-scoped datetime import; add_months has no defensive clamp for non-first-of-month callers. |

*Audited clean, for balance:* no rate limit below 2 s anywhere; no
unbounded pagination; encoding handling deliberate (backslash-yen handled
where Shift_JIS glyphs appear); merch/drink amounts consistently excluded
from min-price; orchard_hall's glyph-based partial-sellout logic notably
careful; all 7 broad `except Exception` sites follow the intentional
"primary loud, optional best-effort" pattern.

### 3.5 Cross-source data quality

| ID | Sev | Finding |
|---|---|---|
| DUP-1 | P1 | **26 same-day/same-venue duplicate groups are live on the site.** Root causes reproduced against the real pairs: (a) `norm_key` keeps single spaces, so titles differing only by internal spacing don't substring-match (Disney Sea concert, Kiramune); (b) NFKC folds `～`(FF5E)→`~` but leaves `〜`(U+301C), so wave-dash variants mismatch (Monster Hunter orchestra); (c) `_artist_overlap` (promoters.py:45-63) never checks *venue title inside promoter text* — only promoter names in venue text and venue **lineup** in promoter text — so "超特急" (venue) vs "超特急 東京ドーム公演" (promoter) fails. |
| DUP-2 | P1 | **No promoter↔promoter dedupe.** The merge only folds promoter rows into venue-source rows; two promoters co-listing one show at a gap venue (Budokan-class) both export standalone. Also observed: same promoter emitting two rows for one show (disk_garage × プリキュア/Monster Hunter — may be legitimate two-performances-per-day; needs care, see R6). |
| DUP-3 | P1 | **Artist index pollution**: promoter scrapers set `lineup=[full show title]` when no act is parsed; 49 "artists" are title-strings ("東京ディズニーシー®25周年…"). They get artist chips/pages. |
| DUP-4 | P2 | JA↔EN cross-script duplicates (venue "アン・ウィルソン" vs promoter "ANN WILSON") can only merge via curated aliases — expected, but worth an alias whenever one surfaces in the dupe report. |

### 3.6 Frontend

| ID | Sev | Finding |
|---|---|---|
| FE-1 | P1 | **`javascript:`-scheme URLs are not blocked** anywhere between scraped `href` and rendered `<a href>` (esc() only escapes HTML metachars; scrapers `urljoin` whatever the page had). A compromised venue page could plant a link that executes on click. One confirmed unescaped interpolation exists too: the venue dot letter (index.html:575-576) renders scraped `venue_name[0]` without esc() — impact capped at one char, but it's the routinely-exercised fallback path for uncurated venues. |
| FE-2 | P1 | **Keyboard/screen-reader dead end**: artist/venue links are `<span class="alink">` (no tabindex/role), day-collapse headers have `role="button"` but no tabindex/keydown, back button is `<a>` without href. Mouse-only navigation for the core flows. (Baseline is otherwise good: real buttons for chips, aria-pressed, focus-visible, reduced-motion.) |
| FE-3 | P2 | No incremental rendering: "All dates" renders 1,951 cards (~tens of thousands of DOM nodes) in one `innerHTML` write; slow-mobile risk. `content-visibility:auto` + chunked append is cheap to add. |
| FE-4 | P2 | No OGP/meta description/JSON-LD/sitemap/robots; `<title>` never changes per route. Link unfurls (LINE/Twitter — the JP audience) show an empty shell. Already on the long-term roadmap; concrete gaps now enumerated. |
| FE-5 | P3 | `title_en` fill is ~1% — the EN toggle switches chrome but content stays Japanese. models.py explicitly reserved flagged machine translation as the path. Owner policy call. |

### 3.7 Tests & repo hygiene

| ID | Sev | Finding |
|---|---|---|
| TST-1 | P2 | Untested critical behaviors: pipeline found=0 loudness (the primary structural canary), DETAIL_CAP shared-budget arithmetic (pipeline.py:229-244), `upsert` AUTO-default changed-content branch (db.py:147-148), `list_events` filter kwargs (the whole CLI list surface), export filtering (EXP-5). |
| TST-2 | P2 | 7 source_ids ride hall-parameterized modules with no own-hall fixture: `owest`, `ocrest`, `onest`, `zepp_shinjuku`, `zepp_yokohama`, `zepp_haneda` (config-only), `billboard_yokohama`. Shared parse code is proven once per module, not per hall config. |
| TST-3 | P2 | `cli.py` has zero tests, and two small bugs: `open(args.report, "w")` without `encoding="utf-8"` (mojibake/UnicodeEncodeError risk on Windows — reports carry JP venue strings), and `--only` with a typo'd source id silently runs nothing. |
| HYG-1 | P2 | Fixture hygiene: one unscrubbed Rails CSRF token (`festival_sweet_love_shower_live.html:13` — the same tag IS redacted in the disk_garage fixtures, so scrubbing is inconsistent); `shozokan_live.json` is 921 KB (2.7× next-largest; a trimmed slice would do); Datadog public RUM token in ultra_japan fixture is fine (designed-public). No live secrets found — the Google Maps key incident class is clean. |
| HYG-2 | P2 | Stray tracked file `=` (0 bytes, committed accidentally in 11237c6). `.gitignore` misses `scrape_report.json` / `r.json` / `*.db-journal`. No `conftest.py` — every test file repeats `sys.path.insert`. |
| HYG-3 | P2 | README.md is badly stale: says 13 sources (it's 82), pre-artists/pre-festivals roadmap, outdated AUTO_PUBLISH guidance. It's the public face of the repo. |

### 3.8 Latent design traps (documented, not currently biting)

- **Pending-forever trap**: with CI running `--auto`, an event first
  created `pending` by a *local* validation scrape stays `pending` forever
  (unchanged upserts don't touch status; changed upserts under AUTO
  default *keep* current status). Today's DB has 0 pending so the owner's
  onboarding flow handles it — but one forgotten approval and events
  silently never publish. R18's bulk-approve + a pipeline-report
  "pending upcoming" counter close it.
- **Stale-upcoming events keep publishing.** Two GLAY shows (udo_artists,
  start 07-31/08-01) were last seen by their scraper on 07-23 yet still
  export — if the promoter pulled them (cancellation?), the site won't
  notice until the dates pass. See R8.
- **LLM genre response truncation**: `_llm_call` caps `max_tokens=1500`
  for a 30-event batch; a truncated JSON reply is silently dropped (re-
  tried next run). Harmless but wasteful once the key lands — bump to
  ~2000 or batch=20 when enabling (R11).
- **`genre_cache` is keyed by event id** — a retitled event keeps its old
  cached verdict. Acceptable; note it.
- **Artist title-matching runs only for music categories** (artists.py:178)
  — correct by design; a musician headlining a museum event won't link.

## 4. Roadmap — ordered, one item at a time

Effort: **S** ≤ half a day · **M** 1–2 days · **L** multi-day.
Every item is one PR with acceptance criteria. Order within a phase is
the recommended order; items don't block each other unless noted.

### Phase 0 — stop the bleeding (ops + feed)

**R1. Make the daily data commit un-loseable.** *(OPS-1 · S)* ✅ **shipped 2026-07-27**
As implemented (`scripts/commit_data.sh`, called by the workflow): the
data files are pure outputs of the run, so instead of rebasing (which
conflicts on binary events.db), the script stashes the fresh files,
`fetch` + `reset --hard origin/main`, restores them, commits, pushes —
retrying the whole cycle ×3 on a pure push race. Conflicts are
impossible by construction; untracked `scrape_report.json` survives the
reset. The issue-filing step is guarded `!cancelled()` so even an
exhausted-retries failure (exit 1, red run) can't suppress error
reporting. *Deliberate deviation from the original sketch:* the
upload-pages/deploy steps stay success-gated rather than `always()` —
on push-triggered runs the pytest step is the deploy gate, and
`always()` there would deploy untested code; with the commit step no
longer able to fail on conflicts, the gate costs nothing.
*Accept:* a push landed mid-scrape no longer fails the run; the issue
step runs even when the commit step fails. (Verified by local git
simulation of the 07-26 conflict, the no-change case, and a rejected-
push race; see tests in the R1 commit.)

**R2. Triage the mot / what_museum 403s.** *(OPS-2 · S)* ✅ **shipped 2026-07-27**
Triage result: both sites work from a residential IP with the same
honest UA (mot's JSON feed parses 9 shows; what_museum full scrape
yields 4 events end-to-end) — the WAFs block GitHub's datacenter IPs
specifically, starting one day after validation. Per hard rule 2 we
don't evade, and the pipeline lives on Actions → both **retired from
pipeline.SCRAPERS** (registered sources 82 → 80). Scraper classes,
fixtures, tests, and venues.py/genres.py entries kept so stored events
keep resolving until they age out; CLAUDE.md documents the revisit
trigger (next art-phase pass). Rolling issue #5 closes automatically on
the next clean daily run.

**R3. Slim the public feed (one export change).** *(EXP-1/2/3/4 · M)*
In `export_public_json`: (1) filter to `(end_date or start_date) ≥`
JST-today — export-only, the DB keeps full history; (2) drop category
`other`; (3) compact JSON (`separators=(",",":")`); (4) stop exporting
fields the frontend never reads — `price_text`, `id`, `status`,
`ticket_url`, `lat`, `lng`, `tags` — and strip `skipped_venues` +
run-internals from `sources[]`; (5) write `generated_at` timezone-aware
UTC (fixes the footer for every viewer). Add the contract tests (EXP-5):
past-event exclusion, other-exclusion, exact exported field set.
Expected: **3.58 MB → ~1.1 MB (-70%)** and the growth curve flattens to
true forward-looking volume.
*Note:* `id` verified unused by index.html today (artist/venue routes key
on names/venue_key); if a future feature needs stable ids, re-add
deliberately. If the owner would rather *show* price tiers than drop
them, keep `price_text` and render it — decide, don't ship it unread.
*Accept:* feed ≤ 1.3 MB; site renders identically (all three sections,
artist pages, ticket badges); new tests pin the contract.

**R4. JST-correct "today" across the scrape layer.** *(SCR-1 · M)*
Add `textutils.jst_today()` (zoneinfo Asia/Tokyo; Japan has no DST) and
replace all 57 scraper call sites + `db.events_needing_detail` + the
sold-out sweep's SQL `date('now')` (pass the JST date as a bound
parameter) + `infer_year`'s default. Make pipeline/db row timestamps
timezone-aware UTC ISO strings (they currently mix owner-local JST and
runner UTC in the same columns).
*Accept:* grep shows no naive `date.today()`/`datetime.now()` left in
`src/`; a test freezes UTC 22:05 and asserts `jst_today()` is the next
calendar day; month-walk start and museum today-filter tests updated.

### Phase 1 — visible data quality

**R5. Fix the promoter-merge matcher (kills most of the 26 live dupes).**
*(DUP-1 · M)*
Introduce a match-normalization used only for overlap testing: `norm_key`
+ strip *all* whitespace + fold `〜/～/~`, curly/corner quotes, `・`, and
`®`-class symbols. Add the missing symmetric check (venue title contained
in promoter haystack, ≥4 chars). Keep the ≥3-char CJK guard. Commit the
audit's dupe-detector as `scripts/find_dupes.py` and diff before/after.
*Accept:* the Disney Sea, Kiramune, Monster Hunter, 超特急, a子, GRe4N
BOYZ, Snugs pairs merge; no previously-merged pair regresses (fixtures
both directions); remaining groups are genuinely distinct events or
cross-script cases (→ R24).

**R6. Promoter↔promoter fold at gap venues.** *(DUP-2 · S-M)*
After R5, extend `_apply` to fold promoter rows against already-emitted
promoter rows at the same (date, venue_key) with the same overlap test —
keep the richer row, union ticket links, OR sold-out. Guard the
two-performances case: only fold when start_times are equal or one side
is missing. Same-source double rows fold under the same rule.
*Accept:* co-promoted gap-venue shows export once; a fixture reproduces
the disk_garage double-row and asserts one event with both ticket links.

**R7. Typed fetch errors + correct month-walk termination.** *(SCR-4 · M)*
In `base.fetch()`: don't retry 4xx (only timeouts/connection errors/5xx),
and raise `NotFoundError` (404/410) distinctly from `FetchError`
(everything else), chaining the original exception. Sweep the ~30
month-walking scrapers: `break` only on `NotFoundError`; let
`FetchError` propagate so a partial 403/429 block surfaces as a loud
per-source error instead of silent coverage truncation. Add a pipeline
test: month 1 OK + month 2 403 ⇒ report error; month 1 OK + month 2 404
⇒ clean stop.
*Accept:* the simulated partial-block test fails loud; normal walk
termination no longer costs 2 retry requests + 6 s per source per day.

**R8. Stale-upcoming (cancellation-shaped) surfacing.** *(GLAY case · S-M)*
An approved/auto event with upcoming `start_date` whose `last_seen` is
≥3 runs old *while its source's latest run succeeded* is suspicious
(removed listing = possible cancellation). Add a section to the run
report + a rolling issue (venue-gap style) listing these; do **not**
auto-hide (month-window scrapers legitimately drop far-future events).
*Accept:* the two GLAY rows (udo_artists, last_seen 07-23) appear in the
next report; doc note tells the owner how to reject a confirmed
cancellation.

**R9. Sold-out latch + churn audit.** *(SCR-2 · S-M)*
In `upsert`, preserve stored `is_sold_out=True` when the incoming listing
row says False (sold-out is sticky until the event passes; the sweep
never un-marks anyway). Then re-measure daily `changed` counts; chase
what remains (bluenote's steady 6–8/day predates this — verify its parse
output is order-stable).
*Accept:* changed/day drops materially; test: listing upsert after a
detail-pass sold-out mark keeps True and reports "unchanged".

**R10. Artist-index pollution guard.** *(DUP-3 · S)*
In `artists._apply`, skip lineup entries whose normalized form equals the
event's normalized title (that's a title, not an act). Fix
disk_garage/livenation lineup population to only emit parsed act names.
*Accept:* the 49 title-string "artists" disappear from the rebuilt index;
清春-style legitimate one-man lineups survive.

**R11. Turn on LLM genre refinement.** *(genre gap · S, owner action)*
The pipeline is built, guarded, cached — and has never run
(`genre_cache`=0; 44% of current music has no genre → invisible to genre
filters). Owner sets the `ANTHROPIC_API_KEY` repo secret; bump
`_LLM_BATCH`→20 or `max_tokens`→2000 to avoid truncation waste; watch the
first runs (≤150 events/run drains the backlog in ~6 days). Alternative
if the owner prefers no-LLM: extend `_VENUE_PRIOR` to the hall/arena
sources and accept coarser tags.
*Accept:* genre coverage on current music >85%; export logs show cache
hits, not repeated calls.

**R12. Decide + implement `title_en`.** *(FE-5 · M, owner policy call)*
The bilingual promise is currently chrome-only (~1% titles). models.py
reserved the path: machine-translated `title_en` **flagged as such**.
Proposal: cached LLM translation at export (same pattern as genres) with
`title_en_mt: true`; frontend shows it in EN mode with a subtle marker;
romaji/latin titles pass through untranslated. If the owner rejects MT,
close this consciously and reframe the EN mode as "EN interface".
*Accept (if go):* >90% of current music events show something readable in
EN mode; the MT flag round-trips to the frontend; no MT text feeds
content_hash (export-time only).

**R13. Price coverage: 円-suffixed amounts + eggman detail override.**
*(SCR-5 · M)*
Extend `parse_prices` to accept `N,NNN円` amounts **only inside labeled
zones** (the existing ADV/前売/料金 window) so merch stays out — same
philosophy as `parse_admission`'s strict-label fallback. Give
`EggmanScraper` a `parse_detail` reusing its own `PRICE_NUM_RE`; spot-
check loft/pia/shibuya_dive/www fixtures for bare-yen detail prices while
there.
*Accept:* price coverage on current music rises measurably (baseline
65%); no fixture regresses to a merch/goods price.

**R14. pia.py parity fixes.** *(SCR-7/8 · S)*
Wrap `PiaArenaMMScraper`'s month-walk like its Toyosu sibling (post-R7:
break on `NotFoundError`), and add the month-context/year cross-check —
hoist the "nearest year to page anchor" logic already implemented six
times elsewhere into `textutils.nearest_year(month, day, anchor)` and use
it here (hulic_hall and opera_city adopt it opportunistically).
*Accept:* fixture with a spillover adjacent-month row parses to the
correct year; a short month-walk no longer errors the source.

**R15. One date-range parser.** *(SCR-9 · S-M)*
Collapse the three copies (museums.py ×2, mori.py) into one
`textutils._range_from_hits(hits, max_days=3*365)`; the mori dotted-date
fallback gains the missing 3-year sanity guard.
*Accept:* existing museum/gallery fixtures pass unchanged; a synthetic
garbled two-date input that today yields a multi-year range on the
yamatane/sompo/top_museum/OCAG path returns (None, None).

### Phase 2 — hardening

**R16. Close the core test gaps.** *(TST-1/2 · M, splittable)*
(a) pipeline integration: non-allow_empty scraper returning [] sets
`report["error"]`; (b) DETAIL_CAP boundary: 45 candidates → exactly 40
fetches, budget split across backlog+sweep verified; (c) upsert
AUTO-default changed-branch keeps status; (d) `list_events` all four
filters; (e) the 7 per-hall fixtures: owest/ocrest/onest,
zepp_shinjuku/yokohama/haneda, billboard_yokohama.

**R17. Detail-pass attempt memory.** *(SCR-3 · S-M)*
Record detail-attempt count (or last-attempt date) per event; skip events
with ≥2 fruitless attempts unless content_hash changed since. Frees ~40
fetches/day on tokyo_intl_forum alone and shortens the daily run.
*Accept:* steady-state daily detail fetches drop; the backlog report
distinguishes "never tried" from "tried, page has nothing".

**R18. CLI quality-of-life.** *(TST-3, pending-trap · S)*
`--report` writes with `encoding="utf-8"`; unknown `--only` ids error
loudly listing valid ids; `approve --source X --all-pending` bulk mode;
`cli.py status` printing source_health + pending-upcoming +
stale-upcoming counts (R8's data) for a one-command morning check.

**R19. Repo hygiene batch.** *(HYG-1/2/3, OPS-3/4/5/6 · S)*
`git rm =`; extend .gitignore (scrape_report.json, r.json, *.db-journal);
scrub the sweet_love_shower CSRF token (match the disk_garage redaction
style); trim `shozokan_live.json` to a representative slice; add
`tests/conftest.py` with the sys.path insert (drop ~50 copies); rewrite
README (82 sources, current architecture, point here); pin dependencies
(`requirements.lock` consumed by CI; floors stay in requirements.txt);
job-level workflow permissions; bump checkout/setup-python; guard the
export step on push-triggered runs.

**R20. Scraper consolidation batch.** *(SCR-10/11/12/14/15 · M)*
Mechanical, behavior-preserving: extract the ex_theater/sgc_hall twin
into a shared JSON-feed-hall helper; hoist `keep_richest`,
`MULTI_ACT_RE`, the #date-fragment algorithm, and 2–3 label-map helpers
into textutils; delete the four dead `infer_year(today.month, …)`
fallbacks; fix club_citta's `or soup`; per-URL try in festivals lineup
fetching; cap udo detail fetches; the small cleanups (shadowed `import
re`, `raise … from`, function-scoped datetime import, add_months clamp).
*Accept:* pytest green with zero fixture changes; net-negative LOC diff.

**R21. Frontend safety + a11y.** *(FE-1/2 · S-M)*
(a) URL scheme allowlist: at export drop non-http(s)
`source_url`/ticket URLs (belt), and in the frontend blank non-http(s)
hrefs (suspenders); esc() the venue dot letter. (b) Keyboard nav: make
alinks real `<a href="#/artist/…">`, day-heads `tabindex="0"` +
Enter/Space handler, back button a real link.
*Accept:* tab-only walkthrough reaches artist page → back → venue page;
a `javascript:alert(1)` URL planted in a fixture never renders as a
clickable href.

**R22. Frontend render cost.** *(FE-3 · S-M)*
`content-visibility:auto` + `contain-intrinsic-size` on cards/day
sections; chunk the "All dates" render (append day groups in
requestAnimationFrame batches). Re-measure on a mid-tier phone profile.

### Phase 3 — strategic (decide, then build)

**R23. Data-in-git growth strategy.** *(OPS-7 · decision)*
Options when the repo gets heavy: (a) keep as-is until it hurts (valid —
GitHub's ~1 GB soft limit is years away at current rate, and R3 shrinks
public.json churn); (b) move data commits to an orphan `data` branch;
(c) periodic history squash of data commits; (d) stop committing
events.db, persist via Actions artifacts (weakens the git-scraping audit
trail — probably not worth it). Recommendation: (a) now, revisit at
~100 MB pack size; document the choice in CLAUDE.md.

**R24. Cross-script alias curation loop.** *(DUP-4 · standing, S each)*
Whenever `scripts/find_dupes.py` (R5) shows a JA↔EN pair (ANN WILSON /
アン・ウィルソン), add the `CURATED_ALIASES` / `_EXTRA_ALIASES` entry.
Cheap, compounding payoff for artist pages and merges.

**R25. Discoverability pack.** *(FE-4 · M)*
Meta description, OGP + Twitter/LINE card tags, JSON-LD `Event` for the
first N upcoming events (emitted at export), robots.txt + sitemap.xml,
per-route `document.title`. Hash-based routes limit deep SEO — either
accept homepage-only or promote top routes to query paths first. Custom
domain when the owner buys one.

**R26. iCal export.** *(M)*
`cli.py export --ical` writing `site/events.ics` (all upcoming music)
plus per-venue `site/ical/<venue_key>.ics`; link from venue pages.
Pure-stdlib generation, fixture-tested; mind escaping and JST TZID.

**R27. Daily-run scale-out (only when needed).** *(P3)*
32 min is fine today; R7 + R17 will shave several minutes of pure waste
first. If source count doubles: parallelize *across* sources with a
small worker pool (per-host politeness already lives per-scraper;
different domains don't share budgets). Keep 2 s per-host floors; cap
pool at ~4.

**R28. Source expansion (standing, curated).** *(venue-coverage doc)*
Watchlist already documented there and in CLAUDE.md: Shiseido Gallery
(fall dates), Sonic Mania (next season), Blue Note Jazz Fes 2026, Kinema
Club + Ueno Royal revisits, Watari-um TLS recheck, Bunkamura/Idemitsu on
reopening, RUIDO family / SALOON / more TDP JSON feeds, flowers autumn
list (Aug–Oct announcements). Each new source follows the validation
workflow + fixture tests.

### Suggested sequencing at a glance

| Order | Items | Theme |
|---|---|---|
| Week 1 | R1 R2 R3 R4 | reliability + feed correctness |
| Week 2 | R5 R6 R7 | dedupe + silent-failure elimination |
| Week 3 | R8 R9 R10 R11 | freshness, churn, artists, genres |
| Then | R12–R15 | coverage & parser correctness |
| Ongoing | R16–R22 in gaps · R23–R28 as decided | hardening · strategy |

## 5. Explicitly rejected / deferred

- **Auto-hiding stale events** (R8 surfaces instead): month-window
  scrapers make "not seen recently" ambiguous; silently removing a real
  show is worse than briefly listing a cancelled one.
- **Runtime robots.txt fetching**: the house rule checks at onboarding;
  per-run fetches cost requests and change nothing for a fixed source
  list.
- **Automated romanization for artist merging**: ruled out by the owner
  (too ambiguous); curated aliases only (R24).
- **Bypassing the mot/what_museum WAF** (UA rotation, proxies):
  prohibited by hard rule 2, full stop.
- **Splitting the frontend into a build-step app**: the single-file,
  no-build frontend is a feature (deploy = copy); R21/R22 fit within it.
