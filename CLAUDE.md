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

## Registered sources (82, all live-validated with fixture tests)

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
| Promoters (2026-07-14/15) | sogo_tokyo creativeman smash_jpn udo_artists disk_garage livenation_jp | promoters' own calendars — a PRIMARY source for their productions; covers gap venues (Budokan, Kinema Club, 東京体育館, ZOZO Marine, KANDA SQUARE HALL, Belluna Dome, Pacifico, Suntory Hall...) and carries sold-out badges; venue strings stored RAW, resolved + deduped against venue sources at export (venues.py + promoters.py); unresolved venue strings skipped — extend venues.CANONICAL to admit new halls. disk_garage + livenation_jp were onboarded on explicit OWNER approval 2026-07-15 (rule 2's ticketing-page ban does not cover them per owner); livenation_jp = JSON API with CountryIds=110, sold-out from allTicketStatus==3. Skipped: Kyodo Tokyo (WAF 403s our UA — we don't bypass bot detection) |
| Museums / galleries (ART phase, 2026-07-26) | mori_art_museum mori_arts_center_gallery tnm mot nact artizon tobikan nmwa nezu yamatane sompo design_sight_2121 mitsui panasonic_shiodome top_museum shozokan | category "art", date-RANGE events, vclass museum; frontend music/art section toggle renders ranges (on view now sorted by closing date / upcoming). Mori pair = one CMS template (scrapers/mori.py; /jp/+/en/ joined on slug → bilingual titles; relative href = own exhibition). Rest in scrapers/museums.py sharing parse_jp_date_range (+dotted fallback via mori for yamatane/sompo): tnm = top page (list controller redirects there; 展示/予告 labels; p.desc ejected from invalid h3 nesting), mot = public JSON feed /json/exhibitions/exhibitions.json (archive → today-filter), nact = time[datetime] attrs, artizon = linkBlockHover cards (concurrent floor shows normal), tobikan = full-archive listing (today-filter), nmwa = current.html + upcoming.html exb_info sections (permanent/fuzzy-end runs skipped), nezu = year-schedule page (today-filter), yamatane = /exhibitions/ where -open cards have NO inline date → bounded detail fetches pull dt会期/dd (archive cards carry dotted dates), sompo = index top/next blocks (dotted dates), design_sight_2121 = /program/ summaryArea h4+h5, mitsui = index.html + next.html one-show pages (dl 会期 kanji; p.period slash dates fallback), panasonic_shiodome = meta-refresh hub → FY page (終了 label is a template artifact on ALL rows — today-filter decides; page carries next FY too), top_museum = top-page slider cells (dt em.main+em.sub; js-holiday-date data-date attrs are machine-readable; /movie/ screenings excluded), shozokan = WP REST /wp-json/wp/v2/exhibitions (exhibition_period_from/to; other-venue stagings excluded; allow_empty — closed until the 令和8年秋 grand opening, self-arms when dates publish). **Art facets** (models.ART_GENRES, reusing genres[]): tagged at export by genres.art_genres — title-keyword rules FIRST, then venue collection prior (mixed halls tobikan/NACT stay prior-less); deterministic, no LLM; frontend genre row is section-aware. **Admission detail pass**: parse_detail on all art scrapers (except opera_city_gallery — JS shells) lifts the ADULT (一般) admission or 入場無料 via textutils.parse_admission — both printing orders ("一般 1,500円" AND "2,400円（一般）"), 当日 preferred over 前売, bare-price fallback only for strict labels (入館料/入場料/観覧料 — bare 料金 is too noisy, that's how Fate/GO's ¥6,500 goods ticket almost got in); price_min stores the adult price for art (NOT the cheapest tier); unknown stays honest (OCAG shells, collection-ticket shows, TBA); events_needing_detail treats is_free as enrichment and keys on (end_date or start_date) >= today so on-view range events stay enrichable |
| Galleries / art spaces (2026-07-26) | opera_city_gallery what_museum ggg | scrapers/galleries.py; vclass gallery (ggg) joins museum in the art view. opera_city_gallery = public pages are JS shells; content fragments at /contents/exhibition/current+upcoming (robots wildcard rule is commented out = allowed); identity canonicalized to detail.php?id=N from the item's image path so upcoming→current keeps ONE identity (upcoming items have no anchors). what_museum (Tennoz, Warehouse TERRADA) = list cards carry start dates only → bounded detail fetches read the 会期 row (th/td OR dt/dd — template varies per show); today-filter. ggg = top-page box-information (ttl02 title; 詳細 link sits outside the box) |
| Seasonal curated: matsuri + hanabi + flowers (2026-07-27) | matsuri hanabi flowers | scrapers/matsuri.py — the festivals pattern for categories "festival" (matsuri) and "fireworks": curated SeasonalEdition config, dates verified against official pages when added; scraper fetches NOTHING (allow_empty; finished editions self-sunset; non-contiguous dates like 酉の市 zodiac days or Yokohama Night Flowers series → one event per date with #anchors). Each edition IS its venue identity (vclass matsuri; promoters.py assigns venue_key like festivals). genres[] carries the section type facet (models.SEASONAL_GENRES matsuri/hanabi, set by the scraper). Frontend third section まつり・花火 (day-group rendering, 公式サイト links, type filter in the genre row). 11 matsuri + 12 hanabi editions live incl. 深川八幡 本祭 year, あつぎ鮎まつり moved Aug→Oct 2026. **Flowers** (owner-decided 2026-07-27): ONLY organizer-dated events, no bloom forecasts — "best time to see X" is a prediction, not a fact; the dated festival entry itself carries the season signal. (Owner may revisit and add spots/best-time guidance later; if so, keep it clearly separated from the factual event feed.) Venue identity = the GARDEN (multiple events/yr share it), not the event. 2 seeded (向島百花園 萩まつり via metro press release, 日比谷ガーデニングショー); 9-item autumn watch list with announcement leads documented in FLOWER_EDITIONS comments — web-search AI summaries have been caught relabeling 2025 runs as "2026", confirm on the venue's own page. Watch: 神田古本まつり (jimbou.info 2026 page pending), Yokohama Night Flowers Oct+ dates (announced Aug), 隅田川+立川昭和記念公園 next season |
| Festivals (2026-07-14, expanded 07-26) | festivals | curated ACTIVE_EDITIONS config (dates = facts, lineups scraped): Fuji Rock, Summer Sonic Tokyo, Rock in Japan, Sweet Love Shower, Ultra Japan, Countdown Japan skeleton, @JAM EXPO (Nuxt SPA — lineup via its public JSON API, Live-Nation-style), a-nation + Local Green skeletons (lineups unannounced; extractor patterns documented in festivals.py); allow_empty=True (seasonal); category music_festival; the festival IS the venue identity (vclass festival); DORMANT_EDITIONS documents finished editions for next-season curation (incl. POP YOURS — JS shell, needs API recon; PUNKSPRING — 2026 never announced) |

Checked and NOT scrapeable (2026-07-13): Budokan (official site
publishes no concert listings), Hibiya Yaon (closed for reconstruction),
Koenji HIGH + Tokyo Taiikukan (robots.txt disallow), Pacifico Yokohama
(no public schedule), Tokyo Kinema Club (kinema.tokyo calendar empty —
revisit). Festivals checked 2026-07-26: Tokyo Idol Festival
(official.idolfes.com robots.txt names ClaudeBot in its disallow — out
per rule 2), Slow LIVE (host red-hot.ne.jp robots is default-deny
allowlist — out), Sonic Mania (no 2026 edition on summersonic.com;
"MIDNIGHT SONIC" tokyo-midnight-day1/2 pages exist instead — revisit
next season), Blue Note Jazz Festival Japan (bluenotejazzfestival.jp
still shows 2025; watch for 2026 dates — scrapeable when announced),
Knotfest Japan (hiatus since 2023), Download Japan (no own site; its
Makuhari shows ride in via creativeman/livenation_jp promoters).
Museums checked 2026-07-26: Suntory Museum of Art (suntory.co.jp WAF
403s our honest UA — out per rule 2), Tokyo Station Gallery (ejrcf.or.jp
WAF 403s too — out), Watari-um (TLS certificate broken on both hosts;
we never disable verification — recheck later), Ueno Royal Museum
(schedule is a JS calendar over article.cgi ids — revisit), Ghibli
Museum (/exhibition/ is a blog-style archive: start dates only,
years-old entries still listed — current-vs-ended not extractable as
fact), teamLab (permanent installations, not date-range events),
Idemitsu Museum of Arts (closed for the Teigeki building rebuild; site
lists only past exhibitions), Bunkamura ザ・ミュージアム (休館中 during
the Shibuya renovation; off-site shows only — revisit on reopening).
Galleries checked 2026-07-26: TERRADA ART COMPLEX (WAF 403s our honest
UA), Complex665 (domain no longer resolves — building's galleries
dispersed), POLA Museum Annex (WAF 403), Shiseido Gallery (news-feed
top page; year schedule carries no dated upcoming rows yet — recheck
when the fall show is announced).
Future family leads: RUIDO group (Akabane/Yokohama ReNY...),
SALOON (saloon-tokyo.com, UNIT's sister floor), other TDP JSON feeds.

## Hard rules

1. **Facts only.** Store titles, dates, times, prices, venue, lineup,
   ticket links, source URL. NEVER copy event descriptions or images from
   source sites — link out. This is a legal + relationship principle.
2. **Politeness.** Keep `rate_limit_s >= 2`, identifiable User-Agent,
   detail-fetch caps. Check robots.txt before adding any new source.
   Never scrape ticketing companies' own aggregation pages (e+ live house
   listings, Pia listing pages). Official venue sites, promoters' own
   calendars, and platforms the owner explicitly approves (DISK GARAGE,
   Live Nation Japan — approved 2026-07-15) are all fair game. Never
   bypass bot detection: a site that 403s our honest UA stays skipped.
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
   frontend artist pages match the canonical `artists[]` field).
   JA/EN alias merging DONE 2026-07-26: artists.py CURATED_ALIASES —
   hand-curated only (romanization is too ambiguous to automate); both
   lineup entries and title matches collapse into the canonical act.
   Extend the table as new spelling pairs surface.
3. ~~Venue build-out (live houses, halls, arenas)~~ DONE 2026-07-13 —
   see the source table above; leads for later: RUIDO family, SALOON,
   more TDP feeds. `docs/venue-coverage-roadmap.md` has per-venue notes.
4. LLM-assisted genre tagging in the pipeline (facets in models.GENRES) —
   rule+prior+cached-LLM tagging exists at export; extend as needed.
5. ~~Festivals as a curated source class~~ DONE 2026-07-14, expanded
   07-26 (@JAM EXPO + skeletons; see source table).
6. **ART phase (started 2026-07-26, owner-confirmed next focus):**
   museums/galleries as category "art" date-range events. 19 art
   sources live (16 museums + OCAG/WHAT/ggg — see source tables) with
   the art-facet taxonomy shipped (rules + venue priors at export) and
   the admission-price detail pass live (adult 一般 price / 入場無料).
   Later within the phase: more spaces
   (Shiseido Gallery when fall dates publish, Tokyo Station Gallery +
   Suntory + Watari-um + TERRADA + POLA Annex retries, Bunkamura +
   Idemitsu on reopening). Matsuri + fireworks + flowers DONE
   2026-07-27 as curated seasonal sources (see source table) — every
   category from the original CLAUDE.md list is now live. Flowers
   grows via the seasonal watch list (autumn announcements Aug-Oct;
   spring research pass ~Feb).
7. Later: dedupe across sources (venue aliases: Kanadevia Hall ex-TDC
   Hall, MUFG Stadium ex-国立競技場), iCal export, OGP/sitemap, custom
   domain. New-source AUTO promotion after a few clean daily runs.
