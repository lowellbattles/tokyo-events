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
  the frontend main list shows only "music". `sales[]` (2026-09-24):
  ticket sale windows {kind lottery|presale|general, label, opens,
  closes} in JST ISO → frontend badges (先行受付中 〜M/D / 一般発売 M/D〜)
  and a 先行受付中 When-chip. Pages omit years: `textutils.sale_window`
  infers them from the show date. Filled by creativeman (both
  templates), kajimoto and curated_concerts; NOT by livenation_jp (its tickets[]
  "General Onsale" windows are link-visibility, not real sale dates —
  checked 2026-09-24). Empty `sales` hashes like its absence.
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
  Also CAPACITY: approx max capacity per music venue (curated metadata,
  tests enforce coverage) → export adds top-level
  `venues:{venue_key:{capacity}}` to public.json, powering the frontend
  キャパ size-tier filter (〜300/1K/2K/10K/10K+), the 大きい順
  within-day sort, and venue-page 約N人 display (2026-08-10).
- `src/tokyo_events/announce.py` — 新着 / "Just announced" (2026-09-24):
  export adds optional `announced` (JST date we first saw the show) to
  public.json events; the frontend 新着 chip lists the last 14 days
  grouped by that date and cards carry a NEW tag. Onboarding runs and
  entries in `announce.BACKFILL` are excluded — **whenever a change
  widens coverage (longer horizon, recovered shows, source_url identity
  migration), add a BACKFILL entry** dated the first scheduled run that
  includes it, or the view floods with old shows. Promoter folds keep
  the earliest sighting (backfill = unknown beats any date).
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

## Registered sources (83, all live-validated with fixture tests)

| Family / class | source_ids | Notes |
|---|---|---|
| Liquidroom | liquidroom | |
| O-Group | oeast owest ocrest onest | |
| Zepp | zepp_divercity zepp_haneda zepp_shinjuku zepp_yokohama | month pages `?_y=YYYY&_m=M`, walks 12 months |
| Billboard | billboard_tokyo billboard_yokohama | scraper-set genres |
| Pia | toyosu_pit pia_arena_mm | |
| Shibuya indie (step 3) | quattro_shibuya www www_x duo | |
| Loft group | loft_shinjuku shelter loft_heaven | LOFT9 excluded (talk venue) |
| Live houses (2026-07-13) | unit_daikanyama club_citta eggman shibuya_dive reny_shinjuku que_shimokitazawa yokohama_bay_hall fever_shindaita veats_shibuya club_seata stellar_ball | reny = ruido.org (detail-page-driven, RUIDO family expandable); que = clubque.net (operator changed 2026-07); stellar_ball under princehotels.co.jp |
| Jazz (Blue Note Japan) | bluenote_tokyo cotton_club | jazz-soul prior. cotton_club reservation calendar REDESIGNED 2026-09 (found=0 from 09-01): identity moved to reserve…/schedule/exec/<id> (+#date per night of a run); times + sold-out now via detail pass on the exec page, which is mid-migration between two templates (both parsed). Old artist-slug-URL rows were rejected at the switch |
| Halls / theaters | ex_theater line_cube_shibuya hulic_hall kanadevia_hall sgc_hall_ariake tokyo_intl_forum nhk_hall opera_city tachikawa_stage_garden orchard_hall | ex_theater + sgc_hall = TV-Asahi TDP JSON feeds; tokyo_intl_forum funnels the 8-hall complex to Hall A concerts via the detail pass; hulic = hulic-theater.com |
| Arenas / domes / stadiums | yokohama_arena tokyo_dome tokyo_garden_theater ariake_arena toyota_arena_tokyo k_arena_yokohama yoyogi_gym1 kokuritsu_stadium makuhari_messe yokohama_buntai | tokyo_dome = one static full-year page, concert rows only; makuhari uses the site's own music-category filter (?c=2); kokuritsu = jns-e.com (MUFG naming), moved from the dead /event/ listing to /calendar/ (2026-09, same per-event slugs — source_url identity migration, see announce.BACKFILL) |
| Promoters (2026-07-14/15) | sogo_tokyo creativeman smash_jpn udo_artists disk_garage livenation_jp | promoters' own calendars — a PRIMARY source for their productions; covers gap venues (Budokan, Kinema Club, 東京体育館, ZOZO Marine, KANDA SQUARE HALL, Belluna Dome, Pacifico, Suntory Hall...) and carries sold-out badges; venue strings stored RAW, resolved + deduped against venue sources at export (venues.py + promoters.py); unresolved venue strings skipped — extend venues.CANONICAL to admit new halls. disk_garage + livenation_jp were onboarded on explicit OWNER approval 2026-07-15 (rule 2's ticketing-page ban does not cover them per owner); livenation_jp = JSON API with CountryIds=110, sold-out from allTicketStatus==3. Skipped: Kyodo Tokyo (WAF 403s our UA — we don't bypass bot detection). **Horizon (2026-09-24)**: creativeman/smash_jpn/sogo_tokyo/disk_garage walk 12 months (was 2–3; arena runs announce + presell ~1yr out — RADIOHEAD's Jun-2027 GMO Arena Saitama run was invisible); creativeman tour_fetch_cap 110 covers the horizon (~100 Kanto tours, ~5 min/run) because past-cap tours export venue-less = dropped. Creativeman **microsites** (calendar links off-site, no leg tables): venue = the single curated venue named in title/headings/img alt, else reported as "[no leg table] …" in skipped_venues. **livenation_jp 403'd our UA on every path incl. robots.txt 2026-08-29..09-24**: the block keys on the "python-requests" token, not our bot name — on OWNER approval (2026-09-24) livenation_jp alone sends `base.USER_AGENT_NO_LIB_TOKEN` (same honest name + contact, no library token; NOT a browser disguise). Rule 2 otherwise unchanged; suntory.co.jp still 403s even that |
| Classical promoters (2026-09-24) | kajimoto japan_arts | genres `["classical"]` set by the scraper; category MUSIC. kajimoto = ONE static archive page (`/concerts/schedule/`, 357 shows spanning 2019–2027, no month-walk) filtered to `>= today`, then one fetch per distinct upcoming show's own page (legs grouped by shared tour, e.g. LSO Japan Tour 2026 = Kyoto + 2× Suntory on one page); its own e+ storefront (`w1.onlineticket.jp`, "カジモト・イープラス") is matched via a new `textutils.TICKET_PROVIDERS["onlineticket.jp"] = "eplus"` entry (doesn't contain the literal "eplus.jp"). japan_arts = `/concert` + `/concert/page/N/` walked with an empty-streak stop (pagination is NOT date-ordered and out-of-range pages silently re-serve the last real page rather than 404 — 2 pages already covered the whole 48-show live catalogue 2026-09-24), then one fetch per distinct detail page; a "series/set-ticket" landing page (e.g. アフタヌーン・コンサート・シリーズ) that bundles several already-separately-listed concerts is scraped like any other page rather than special-cased — promoters.py's existing same-source title-overlap fold naturally merges the weaker bundle-page row into the concert's own dedicated-page row at export. **price_min excludes age/status-restricted tiers** (student/U25/child seats — a "¥1,000〜" quoted from an elementary-school-only seat would mislead everyone else): `textutils.is_restricted_tier` catches tiers whose own label says so (学生, U25, ユース, 高校生以下…), `textutils.restricted_tier_names` additionally harvests a promoter's own "◎<name>（…restriction…)" footnote for tiers with no keyword in their own label (japan_arts' "Miyujiシート（小学生限定）"); full tier list still goes in price_text via `textutils.open_tier_min`. Venues added to venues.CANONICAL (vclass hall): kioi_hall (日本製鉄紀尾井ホール, 800 — Kioi Hall's naming-rights rename; old "紀尾井ホール" spelling aliased), hamarikyu_asahi_hall (浜離宮朝日ホール, 552), sumida_triphony (すみだトリフォニーホール, 1801), yokohama_minatomirai_hall (横浜みなとみらいホール, 2020), geigeki_concert_hall (東京芸術劇場コンサートホール, 1999), hakuju_hall (Hakuju Hall, 300; はくじゅホール aliased), daiichi_seimei_hall (第一生命ホール, 700), muza_kawasaki (ミューザ川崎 シンフォニーホール, 2001), suntory_hall_blue_rose (サントリーホール ブルーローズ, 432 — the small recital hall inside the Suntory Hall complex, distinct from suntory_hall's main hall). Nationwide legs a tour also plays (福岡シンフォニーホール, 札幌コンサートホールKitara, 高崎芸術劇場 — Gunma, outside Tokyo/Kanagawa/Chiba/Saitama scope) are left unresolved on purpose |
| Museums / galleries (ART phase, 2026-07-26) | mori_art_museum mori_arts_center_gallery tnm nact artizon tobikan nmwa nezu yamatane sompo design_sight_2121 mitsui panasonic_shiodome top_museum shozokan | category "art", date-RANGE events, vclass museum; frontend music/art section toggle renders ranges (on view now sorted by closing date / upcoming). Mori pair = one CMS template (scrapers/mori.py; /jp/+/en/ joined on slug → bilingual titles; relative href = own exhibition). Rest in scrapers/museums.py sharing parse_jp_date_range (+dotted fallback via mori for yamatane/sompo): tnm = top page (list controller redirects there; 展示/予告 labels; p.desc ejected from invalid h3 nesting), nact = time[datetime] attrs, artizon = linkBlockHover cards (concurrent floor shows normal), tobikan = full-archive listing (today-filter), nmwa = current.html + upcoming.html exb_info sections (permanent/fuzzy-end runs skipped), nezu = year-schedule page (today-filter), yamatane = /exhibitions/ where -open cards have NO inline date → bounded detail fetches pull dt会期/dd (archive cards carry dotted dates), sompo = index top/next blocks (dotted dates), design_sight_2121 = /program/ summaryArea h4+h5, mitsui = index.html + next.html one-show pages (dl 会期 kanji; p.period slash dates fallback), panasonic_shiodome = meta-refresh hub → FY page (終了 label is a template artifact on ALL rows — today-filter decides; page carries next FY too), top_museum = top-page slider cells (dt em.main+em.sub; js-holiday-date data-date attrs are machine-readable; /movie/ screenings excluded), shozokan = WP REST /wp-json/wp/v2/exhibitions (exhibition_period_from/to; other-venue stagings excluded; allow_empty — closed until the 令和8年秋 grand opening, self-arms when dates publish). **Art facets** (models.ART_GENRES, reusing genres[]): tagged at export by genres.art_genres — title-keyword rules FIRST, then venue collection prior (mixed halls tobikan/NACT stay prior-less); deterministic, no LLM; frontend genre row is section-aware. **Admission detail pass**: parse_detail on all art scrapers (except opera_city_gallery — JS shells) lifts the ADULT (一般) admission or 入場無料 via textutils.parse_admission — both printing orders ("一般 1,500円" AND "2,400円（一般）"), 当日 preferred over 前売, bare-price fallback only for strict labels (入館料/入場料/観覧料 — bare 料金 is too noisy, that's how Fate/GO's ¥6,500 goods ticket almost got in); price_min stores the adult price for art (NOT the cheapest tier); unknown stays honest (OCAG shells, collection-ticket shows, TBA); events_needing_detail treats is_free as enrichment and keys on (end_date or start_date) >= today so on-view range events stay enrichable |
| Galleries / art spaces (2026-07-26) | opera_city_gallery ggg | scrapers/galleries.py; vclass gallery (ggg) joins museum in the art view. opera_city_gallery = public pages are JS shells; content fragments at /contents/exhibition/current+upcoming (robots wildcard rule is commented out = allowed); identity canonicalized to detail.php?id=N from the item's image path so upcoming→current keeps ONE identity (upcoming items have no anchors). ggg = top-page box-information (ttl02 title; 詳細 link sits outside the box) |
| Seasonal curated: matsuri + hanabi + flowers (2026-07-27) | matsuri hanabi flowers | scrapers/matsuri.py — the festivals pattern for categories "festival" (matsuri) and "fireworks": curated SeasonalEdition config, dates verified against official pages when added; scraper fetches NOTHING (allow_empty; finished editions self-sunset; non-contiguous dates like 酉の市 zodiac days or Yokohama Night Flowers series → one event per date with #anchors). Each edition IS its venue identity (vclass matsuri; promoters.py assigns venue_key like festivals). genres[] carries the section type facet (models.SEASONAL_GENRES matsuri/hanabi, set by the scraper). Frontend third section まつり・花火 (day-group rendering, 公式サイト links, type filter in the genre row). 11 matsuri + 12 hanabi editions live incl. 深川八幡 本祭 year, あつぎ鮎まつり moved Aug→Oct 2026. **Flowers** (owner-decided 2026-07-27): ONLY organizer-dated events, no bloom forecasts — "best time to see X" is a prediction, not a fact; the dated festival entry itself carries the season signal. (Owner may revisit and add spots/best-time guidance later; if so, keep it clearly separated from the factual event feed.) Venue identity = the GARDEN (multiple events/yr share it), not the event. 2 seeded (向島百花園 萩まつり via metro press release, 日比谷ガーデニングショー); 9-item autumn watch list with announcement leads documented in FLOWER_EDITIONS comments — web-search AI summaries have been caught relabeling 2025 runs as "2026", confirm on the venue's own page. Watch: 神田古本まつり (jimbou.info 2026 page pending), Yokohama Night Flowers Oct+ dates (announced Aug), 隅田川+立川昭和記念公園 next season |
| Hand-entered concerts (2026-09-24) | curated_concerts | scrapers/curated.py — for shows whose official site blocks our honest bot (rule 2) and that no scrapable promoter carries (Suntory Hall's own 主催公演). Each CuratedConcert is typed in after reading the OFFICIAL page in a browser; keeps that page as source_url + a `verified` date; fetches nothing; finished nights sunset; allow_empty; promoter-class (venue resolved at export, folds with any scraped duplicate). price_min = cheapest tier open to everyone (U25/学生 excluded). Re-verify entries when touching the file. Live: ヨーヨー・マ サントリーホール40周年 10/25 + 10/27 (both sold out) |
| Festivals (2026-07-14, expanded 07-26; NATIONWIDE 2026-08-03 per owner) | festivals | curated ACTIVE_EDITIONS config (dates = facts, lineups scraped): Fuji Rock, Summer Sonic Tokyo, Rock in Japan, Sweet Love Shower, Ultra Japan, Countdown Japan skeleton, @JAM EXPO (Nuxt SPA — lineup via its public JSON API, Live-Nation-style), a-nation + Local Green skeletons (lineups unannounced; Local Green '26 relaunches FREE-admission). **Nationwide since 2026-08-03** (owner call — festivals nationwide, venue coverage stays Kanto): LuckyFes (Ibaraki, fespli-platform SSR extractor) + RISING SUN in EZO (Hokkaido, positional day-split extractor — day headers are empty-alt images) live with lineups; verified-date skeletons for WILD BUNCH FEST. (Yamaguchi), Sky Jamboree (Nagasaki), MONSTER baSH (Kagawa), RUSH BALL (Osaka), りんご音楽祭 (Nagano — year-scoped /fes2026/ URLs only, the year-less path serves an orphaned old lineup), FFKT (Izu Shirahama, ex-Nagano), GMO SONIC 2027 (April, GMO Arena Saitama — /lineup/ still serves the FINISHED Jan-26 roster, cross-check dates before targeting), JOIN ALIVE 2027 (Hokkaido). allow_empty=True (seasonal); category music_festival; the festival IS the venue identity (vclass festival); DORMANT_EDITIONS documents finished editions for next-season curation (incl. POP YOURS, PUNKSPRING, OSAKA GIGANTIC — clean article#dayMMDD structure, ARABAKI — recheck Nov/Dec, 京都大作戦 + NUMBER SHOT — poster alt-text names, FUJI & SUN — evergreen domain). Nationwide sweep verdicts 2026-08-03: Sunset Live OUT (successor sunsetlive.jp WAFs every fetch incl. robots.txt — rule 2), BAYCAMP unreachable (DNS dead, no 2026 edition found), OTODAMA ambiguous (robots.txt 403s while content 200s — owner call), 森道市場 not extractable (image-only timetable), ONE MUSIC CAMP postponed to 2027 |

**Venue month-walk horizon (2026-09-24, owner call):** every month-walking
music-venue scraper (Liquidroom/Zepp/Billboard/Pia/Shibuya-indie/Loft-style
families, live houses, halls/theaters, arenas/domes/stadiums, plus Cotton
Club) now defaults to `months_ahead=12` (was 2–9 per venue) via one shared
`scrapers/textutils.HORIZON_MONTHS` constant — big shows go on sale up to a
year out, matching the promoter-scraper horizon bump above. Five scrapers
(eggman, fever, que, tif, unit) previously stopped their walk at the FIRST
empty/no-stub month, which could hide a later-booked month behind one quiet
one; they now match the rest of the codebase's `empty_streak >= 3`
convention. billboard and liquidroom had no `NotFoundError` guard on their
month fetch at all (fine at a 2–3 month walk, a real crash risk at 12) and
now stop cleanly on a 404 like their siblings. Live-probed 2026-09-24: no
scraper needed capping below 12 — venues with nothing booked that far out
already stop early via their own `NotFoundError`/`empty_streak` logic
(confirmed via request-count reasoning, not just event counts, to rule out
a site silently re-serving the same "current month" page for a param it
ignores) rather than walking uselessly to month 12.

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
Retired 2026-07-27 (runner-IP WAF): mot (東京都現代美術館, JSON feed
/json/exhibitions/exhibitions.json) and what_museum (WHAT MUSEUM,
Tennoz) — both began 403ing GitHub Actions runner IPs one day after
validation. Verified same day from a residential IP with the same
honest UA: mot parses 9 shows, what_museum full scrape yields 4 — the
block is datacenter-IP-based, and per rule 2 we don't evade.
Deregistered from pipeline.SCRAPERS; scraper classes, fixtures, tests
and venues.py/genres.py entries kept (stored events resolve until they
age out). Revisit on the next art-phase pass in case the WAFs relax.
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
   museums/galleries as category "art" date-range events. 17 art
   sources live (15 museums + OCAG/ggg — see source tables; mot +
   what_museum retired 2026-07-27, runner-IP WAF) with
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
