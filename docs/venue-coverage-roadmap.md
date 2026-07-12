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
| LOFT9 / Loft Heaven | Shibuya | lower priority — not built |

## Independents — mid-size live houses (1 scraper each) 
| Venue | Area | Status | Notes |
|---|---|---|---|
| LIQUIDROOM | Ebisu | ✅ | built + tested |
| Toyosu PIT | Toyosu | ✅ data captured | index lacks times → detail-page pass |
| Shibuya CLUB QUATTRO | Shibuya | ✅ | ?ym= month pages; NO Kawasaki hall exists |
| duo MUSIC EXCHANGE | Shibuya | ✅ | month pages; no detail pages (day anchors) |
| WWW / WWW X | Shibuya | ✅ | one page, data-place attr splits the halls |
| Daikanyama UNIT | Daikanyama | 🔍 | *added — club/live hybrid* |
| EX THEATER ROPPONGI | Roppongi | 🔍 | *added — ~1,700 cap, TV Asahi-run* |
| Blue Note Tokyo / Cotton Club | Aoyama / Marunouchi | 🔍 | *added — jazz; strengthens genre coverage* |
| Shinagawa Stellar Ball | Shinagawa | 🔍 | *added — idol/anime heavy* |
| 日比谷野外音楽堂 (Hibiya Yaon) | Hibiya | 🔍 | *added — iconic outdoor; seasonal* |
| CLUB CITTA' | Kawasaki | 🔍 | *added — ~1,300 cap, metal/international* |
| shibuya eggman | Shibuya | 🔍 | user add — small (~350), indie/idol |
| SHIBUYA DIVE | Shibuya | 🔍 | user add — newer venue; confirm official schedule page |
| 新宿ReNY | Shinjuku | 🔍 | user add — visual-kei/idol heavy; sister 赤羽ReNY alpha may share platform |
| 下北沢Que | Shimokitazawa | 🔍 | user add — pairs with SHELTER for Shimokita indie depth |
| 東京キネマ倶楽部 | Uguisudani | 🔍 | user add — grand-cabaret hall; visual-kei/idol/retro |
| 横浜ベイホール | Yokohama | 🔍 | user add — ~1,000 cap live house |

## Halls & theaters (seated, 700–2,500)
| Venue | Area | Status | Notes |
|---|---|---|---|
| LINE CUBE SHIBUYA | Shibuya | 📋 | |
| Bunkamura Orchard Hall | Shibuya | 📋 | classical/adult prior; EN calendar exists |
| Hulic Hall Tokyo | Yurakucho | 📋 | |
| Kanadevia Hall（旧TOKYO DOME CITY HALL） | Suidōbashi | 🔍 | renamed Apr 2025 via naming rights (through Mar 2028) — store former names as venue aliases; naming-rights churn is constant |
| SGCホール有明 (SGC Hall Ariake) | Ariake | 🔍 | user add — newer hall |
| 東京国際フォーラム Hall A | Yurakucho | 🔍 | *added — 5,000 cap* |
| NHKホール | Shibuya | 🔍 | *added* |
| 東京オペラシティ | Hatsudai | 🔍 | *added — classical* |
| Tachikawa Stage Garden | Tachikawa | 📋 | user list (t-sg.jp) |

## Arenas / domes / mega (event-class venues)
| Venue | Area | Status | Notes |
|---|---|---|---|
| 日本武道館 | Kudanshita | 📋 | **use official site, not japanconcerttickets.com** (third-party aggregator — conflicts with our facts-from-source principle; official schedule incl. martial arts events, filter by type) |
| Tokyo Garden Theater | Ariake | 📋 | user list URL works |
| Tokyo Dome | Suidōbashi | 📋 | official EN schedule page |
| 有明アリーナ (Ariake Arena) | Ariake | 🔍 | *added — 15,000 cap* |
| TOYOTA ARENA TOKYO | Odaiba | 🔍 | *added — opened late 2025, 10,000 cap; growing K-pop/major bookings* |
| Yokohama Arena | Yokohama | 📋 | |
| K-Arena Yokohama | Yokohama | 📋 | EN schedule exists |
| 国立代々木競技場 第一体育館 | Harajuku | 🔍 | *added* |
| 国立競技場（MUFGスタジアム） | Sendagaya | 🔍 | user add — former National Stadium, now under MUFG naming rights; stadium-class concerts (alias field again) |
| 東京体育館 | Sendagaya | 🔍 | user add — Tokyo Metropolitan Gymnasium, ~10,000 for concerts |
| ぴあアリーナMM | Yokohama | 🔍 | *added — sister to Toyosu PIT, likely same platform → free coverage* |
| Saitama Super Arena | Saitama | ⏸ | under renovation (per user) — add on reopening |
| 幕張メッセ | Chiba | 🔍 | *added — Countdown Japan / Summer Sonic home* |

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
