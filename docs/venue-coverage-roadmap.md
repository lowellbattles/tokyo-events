# Venue Coverage Roadmap — Tokyo Events Aggregator

Organizing principle: **scraper families, not individual venues.** Venues run by
the same operator share a website platform, so one scraper covers the whole
group. Building by family maximizes coverage per engineering hour.

Status: ✅ built · 📋 planned · 🔍 needs scrapeability check

**2026-07-02 update:** Families A (Zepp ×4), B (O-Group ×4), and C
(Billboard ×2) are now BUILT into the pipeline alongside Liquidroom —
11 sources total, pending first-run live validation. Venues marked
"user add" below came from the owner's venue document.

**2026-07-12 update:** All 13 original sources live-validated. Step-3
tranche BUILT and live-validated: Shibuya CLUB QUATTRO, WWW, WWW X,
duo MUSIC EXCHANGE, Shinjuku LOFT, 下北沢SHELTER (+6 sources → 19).
Note: there is NO Kawasaki Club Quattro (hall nav verified live:
shibuya/umeda/nagoya/hiroshima) — the sister-venue note below was wrong.

**2026-07-13 update (music build-out COMPLETE):** 40-venue probe sweep +
33 new sources built, tested, live-validated → **53 sources total**.
Every remaining viable venue below is now ✅. Newly discovered during
probes: 下北沢Que moved to its own clubque.net (operator change
2026-07-01, ex-UK PROJECT); ReNY lives at ruido.org (whole RUIDO chain
shares the template — future family); Hulic Hall is hulic-theater.com;
Stellar Ball is under princehotels.co.jp; 国立競技場 entertainment
schedule is jns-e.com (MUFG naming rights); Tokyo Garden Theater is on
shopping-sumitomo-rd.com; Tokyo Dome schedule is actually STATIC (a
full-year single page — the earlier "JS-rendered" note was a
user-agent/fetch artifact). Not scrapeable, verified: Budokan (official
site publishes no concert data), Hibiya Yaon (closed for
reconstruction from 2025-10), Koenji HIGH + 東京体育館 (robots.txt
disallow — politeness rule), Pacifico Yokohama (no public event
schedule), 東京キネマ倶楽部 (kinema.tokyo calendar verifiably empty —
revisit). Future leads: RUIDO family (赤羽ReNY alpha, Yokohama ReNY,
RizM, REX), SALOON (saloon-tokyo.com, UNIT's sister floor), other
TV-Asahi TDP JSON feeds (ex_theater/sgc_hall pattern).

---

## Family A — Zepp chain (1 scraper → 4 Kanto halls) ✅ built
Shared platform at zepp.co.jp; per-hall schedule pages with identical structure
(`[OPEN]/[START]/[PRICE]` blocks, month-parameter URLs ~12 months ahead).
Verified scrapeable via DiverCity.

| Venue | Area | Notes |
|---|---|---|
| Zepp DiverCity | Odaiba | structure verified 2026-07-02 |
| Zepp Haneda | Haneda | same platform |
| Zepp Shinjuku | Shinjuku (Kabukichō Tower) | same platform |
| KT Zepp Yokohama | Yokohama | same platform |

## Family B — Shibuya O-Group / Spotify venues (1 scraper → 4 halls) ✅ built
Shared WordPress platform at shibuya-o.com. O-EAST verified scrapeable.

| Venue | Cap. class | Notes |
|---|---|---|
| Spotify O-EAST | ~1,300 | ✅ data captured |
| Spotify O-WEST | ~600 | same platform |
| Spotify O-Crest | ~250 | idol/indie heavy — good genre-prior venue |
| Spotify O-nest | ~250 | indie |

## Family C — Billboard Live (1 scraper → Tokyo + Yokohama) ✅ built
Clean unified schedule at billboard-live.com/schedules with a `?today=` date
parameter — likely the easiest multi-venue win. Strong genre prior
(jazz/soul/city-pop/international). Osaka hall comes free if geography ever widens.

## Family D — Loft group (1 scraper → 2–3 venues) ✅ built (LOFT + SHELTER)
| Venue | Area | Notes |
|---|---|---|
| Shinjuku LOFT | Kabukichō | ✅ /schedule/loft/schedule/{id} links |
| Shimokitazawa SHELTER | Shimokitazawa | ✅ /schedule/shelter/{id} links (variant) |
| LOFT9 / Loft Heaven | Shibuya | Heaven ✅ (loft_heaven); LOFT9 excluded — talk venue |

## Independents — mid-size live houses (1 scraper each) 
| Venue | Area | Status | Notes |
|---|---|---|---|
| LIQUIDROOM | Ebisu | ✅ | built + tested |
| Toyosu PIT | Toyosu | ✅ data captured | index lacks times → detail-page pass |
| Shibuya CLUB QUATTRO | Shibuya | ✅ | ?ym= month pages; NO Kawasaki hall exists |
| duo MUSIC EXCHANGE | Shibuya | ✅ | month pages; no detail pages (day anchors) |
| WWW / WWW X | Shibuya | ✅ | one page, data-place attr splits the halls |
| Daikanyama UNIT | Daikanyama | ✅ | unit_daikanyama; SALOON floor = separate domain, future |
| EX THEATER ROPPONGI | Roppongi | ✅ | ex_theater; official TDP JSON feed |
| Blue Note Tokyo / Cotton Club | Aoyama / Marunouchi | ✅ | bluenote_tokyo + cotton_club; jazz-soul prior |
| Shinagawa Stellar Ball | Shinagawa | ✅ | stellar_ball; princehotels.co.jp; mixed 2.5D/idol |
| 日比谷野外音楽堂 (Hibiya Yaon) | Hibiya | ⏸ | CLOSED for reconstruction since 2025-10 — add on reopening |
| CLUB CITTA' | Kawasaki | ✅ | club_citta |
| shibuya eggman | Shibuya | ✅ | eggman; daytime + nighttime archives |
| SHIBUYA DIVE | Shibuya | ✅ | shibuya_dive; idol prior |
| 新宿ReNY | Shinjuku | ✅ | reny_shinjuku on ruido.org; RUIDO chain = future family |
| 下北沢Que | Shimokitazawa | ✅ | que_shimokitazawa; NEW domain clubque.net (2026-07 operator change) |
| 東京キネマ倶楽部 | Uguisudani | ⏸ | kinema.tokyo calendar empty (12mo fwd) — revisit |
| 横浜ベイホール | Yokohama | ✅ | yokohama_bay_hall |
| 新代田FEVER | Shindaita | ✅ | fever_shindaita; j-rock prior *(2026-07-13 add)* |
| Veats Shibuya | Shibuya | ✅ | veats_shibuya; Victor Entertainment *(2026-07-13 add)* |
| 吉祥寺CLUB SEATA | Kichijoji | ✅ | club_seata; drink-charge price trap handled *(2026-07-13 add)* |
| 高円寺HIGH | Koenji | ⛔ | robots.txt disallows — politeness rule *(2026-07-13 add, evaluated)* |

## Halls & theaters (seated, 700–2,500)
| Venue | Area | Status | Notes |
|---|---|---|---|
| LINE CUBE SHIBUYA | Shibuya | ✅ | line_cube_shibuya |
| Bunkamura Orchard Hall | Shibuya | ✅ | orchard_hall via my.bunkamura.co.jp program list |
| Hulic Hall Tokyo | Yurakucho | ✅ | hulic_hall; domain is hulic-theater.com |
| Kanadevia Hall（旧TOKYO DOME CITY HALL） | Suidōbashi | ✅ | kanadevia_hall; store former names as venue aliases when dedupe lands — naming-rights churn is constant |
| SGCホール有明 (SGC Hall Ariake) | Ariake | ✅ | sgc_hall_ariake; TDP JSON feed |
| 東京国際フォーラム Hall A | Yurakucho | ✅ | tokyo_intl_forum; complex-wide listing funneled to Hall A concerts via detail pass |
| NHKホール | Shibuya | ✅ | nhk_hall; single static 6-month page |
| 東京オペラシティ | Hatsudai | ✅ | opera_city; fragment endpoint; classical prior |
| Tachikawa Stage Garden | Tachikawa | ✅ | tachikawa_stage_garden (www.t-sg.jp — apex host refuses TCP) |

## Arenas / domes / mega (event-class venues)
| Venue | Area | Status | Notes |
|---|---|---|---|
| 日本武道館 | Kudanshita | ⛔ | official site publishes NO concert listings (martial arts only) — no compliant source; revisit if they add one |
| Tokyo Garden Theater | Ariake | ✅ | tokyo_garden_theater on shopping-sumitomo-rd.com |
| Tokyo Dome | Suidōbashi | ✅ | tokyo_dome; static full-year schedule.html, concert rows only |
| Yokohama Arena | Yokohama | ✅ | own JSON API: /event/{YYYYMM}?_format=json — built 2026-07-12 |
| 有明アリーナ (Ariake Arena) | Ariake | ✅ | ariake_arena; fixed 5-slug pagination |
| TOYOTA ARENA TOKYO | Odaiba | ✅ | toyota_arena_tokyo; Next.js RSC flight payload, text-keyed |
| K-Arena Yokohama | Yokohama | ✅ | k_arena_yokohama; 円-suffix prices need custom detail parse |
| 横浜BUNTAI | Yokohama (Kannai) | ✅ | yokohama_buntai; reopened 2024 |
| パシフィコ横浜 | Yokohama (MM) | ⛔ | no public schedule of external events on pacifico.co.jp |
| 国立代々木競技場 第一体育館 | Harajuku | ✅ | yoyogi_gym1; sports rows → category other |
| 国立競技場（MUFGスタジアム） | Sendagaya | ✅ | kokuritsu_stadium; official entertainment site jns-e.com |
| 東京体育館 | Sendagaya | ⛔ | robots.txt disallows — politeness rule |
| ぴあアリーナMM | Yokohama | ✅ | pia_arena_mm (built earlier in the Pia family) |
| Saitama Super Arena | Saitama | ⏸ | under renovation (per user) — add on reopening |
| 幕張メッセ | Chiba | ✅ | makuhari_messe; site's own music filter ?c=2 |

## Promoters (source class added 2026-07-14) ✅ built
Promoters publish their own productions — a legitimate primary source
that reaches venues we can't scrape directly (Budokan publishes nothing;
Koenji HIGH / 東京体育館 block robots on their OWN sites, but a
promoter's calendar is the promoter's content).

| Promoter | source_id | Notes |
|---|---|---|
| SOGO TOKYO | sogo_tokyo | sogotokyo.com/live_information/calendar/ — month pages, dl/dt detail pages; recovers Budokan, Kinema Club, 東京体育館, 神田明神ホール, ZOZOマリンスタジアム bookings |
| Creativeman | creativeman | /event/?cmy=&cmm= — per-tour pages with multi-leg ticket tables + per-leg SOLD OUT; strong international bookings; prefecture labels give a free Kanto filter |

Overlap policy: export-time merge (promoters.py) — venue-source records
stay authoritative; duplicate promoter rows fold in (sold-out OR,
ticket-link union, gap-fill); gap-venue events stand alone under
venue_key. Candidates for later: Hayashi International (ハヤシ),
Kyodo Tokyo, H.I.P., Live Nation Japan, Smash (Fuji Rock operator —
doubles as the festival lead).

## Festivals (new source class — annual, lineup-wave updates)
Fuji Rock (Naeba) · Summer Sonic (Chiba/Osaka) · Rock in Japan ·
Japan Jam (Chiba) · Countdown Japan (Makuhari) · Sweet Love Shower (Yamanakako) ·
Metrock (Tokyo/Osaka) · Viva La Rock (Saitama) · Synchronicity (Shibuya) ·
Greenroom (Yokohama) · Ultra Japan (Odaiba)
→ Scrape/curate official lineup pages monthly; lineups feed the artist graph.

## Deliberately excluded / cautions
- **Closed venues that still appear in stale lists:** Shinkiba Studio Coast /
  ageHa (closed 2022), 中野サンプラザ (closed 2023). Exclude.
- **livehouse.eplus.jp** — e+ runs a live house schedule aggregator with ticket
  links. Useful as a *cross-check and gap-detection* source, but scraping a
  ticketing company's aggregation likely violates their ToS; prefer official
  venue sites, and treat e+ links as outbound ticket links only.
- **Third-party listing sites** (japanconcerttickets.com, Bands in Town):
  reference only, never primary source.

## Suggested build order
1. **Family A + B + Billboard** (3 scrapers → 10 venues) — biggest coverage
   jump per effort, all verified or high-confidence platforms.
2. **Detail-page pass** for all sources (fills times/prices/ticket links).
3. Quattro, WWW, duo, Loft group (Shibuya/Shimokita indie depth).
4. Halls (Line Cube, Orchard, Hulic) + arenas (Budokan, Garden Theater, Dome).
5. Festivals + artist entity/cross-referencing.
