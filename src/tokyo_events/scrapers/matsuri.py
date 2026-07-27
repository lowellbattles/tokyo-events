"""Curated seasonal events — matsuri (category "festival") and fireworks
(category "fireworks"). Two registry sources, one module:

  - matsuri : traditional festivals (阿波おどり, 例大祭, よさこい, 酉の市…)
  - hanabi  : fireworks displays (花火大会)

Like the music-festival source, these are CURATED EDITIONS: the dates are
annually-announced facts verified against each event's official site when
the entry is added/refreshed — organizer sites are wildly heterogeneous
one-page affairs, so there is nothing worth scraping daily. The scraper
therefore fetches NOTHING: it yields events straight from config, drops
finished editions (self-sunsetting), and reports found=0 out of season
without tripping the loud-zero guard (allow_empty).

Each edition IS its own venue identity (vclass "matsuri" in
venues.CANONICAL — fireworks sites included), mirroring the festival
pattern. The `genres` field carries the section type facet ("matsuri" /
"hanabi", models.SEASONAL_GENRES) so the frontend's section-aware genre
row filters between the two without new machinery.

Curation duties each season (mirror of festivals.py):
  - refresh dates from official sources when the new year's editions are
    announced; entries whose dates have passed simply stop yielding.
  - keep source URLs pointing at the OFFICIAL page that states the date.
  - fireworks: start/end time from the official announcement when
    published; 荒天中止 (weather cancellation) is not modeled — we link
    out and the official page is the authority on the day.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Category, Event
from .base import BaseScraper


@dataclass(frozen=True)
class SeasonalEdition:
    key: str                     # venues.CANONICAL key = venue identity
    title_ja: str
    category: Category           # Category.FESTIVAL or Category.FIREWORKS
    #: consecutive run -> ONE event start..end; a non-consecutive pair
    #: (e.g. 酉の市 zodiac days) lists each date -> one event per date
    dates: tuple[str, ...]
    venue_area: str
    url: str                     # official page stating the date (source_url)
    title_en: Optional[str] = None
    start_time: Optional[str] = None   # fireworks launch time
    end_time: Optional[str] = None     # informational; not exported today
    contiguous: bool = True


def _events_for(ed: SeasonalEdition, source: str, venue_name: str,
                today: str) -> Iterable[Event]:
    genre = "hanabi" if ed.category is Category.FIREWORKS else "matsuri"
    if ed.contiguous:
        runs = [(ed.dates[0], ed.dates[-1])]
    else:
        runs = [(d, d) for d in ed.dates]
    for start, end in runs:
        if end < today:
            continue                       # finished edition/day: sunset
        yield Event(
            source=source,
            source_url=f"{ed.url}#{start}" if len(runs) > 1 else ed.url,
            title_ja=ed.title_ja, title_en=ed.title_en,
            category=ed.category,
            start_date=start,
            end_date=end if end != start else None,
            start_time=ed.start_time,
            venue_name=venue_name, venue_area=ed.venue_area,
            genres=[genre],
        )


class CuratedSeasonalScraper(BaseScraper):
    """One curated seasonal source ("matsuri" or "hanabi"). Fetches
    nothing; config is the fact base."""
    supports_detail = False
    allow_empty = True               # deep winter legitimately has none

    def __init__(self, source_id: str, **kw):
        self.source_id = source_id
        self.source_name = ("Tokyo matsuri (curated)"
                            if source_id == "matsuri"
                            else "Tokyo hanabi (curated)")
        super().__init__(**kw)

    def _editions(self) -> tuple[SeasonalEdition, ...]:
        return (MATSURI_EDITIONS if self.source_id == "matsuri"
                else HANABI_EDITIONS)

    def scrape(self, today: Optional[str] = None) -> Iterable[Event]:
        from ..venues import display_of
        today = today or dt.date.today().isoformat()
        for ed in self._editions():
            venue_name = display_of(ed.key) or ed.title_ja
            yield from _events_for(ed, self.source_id, venue_name, today)

    def parse(self, html: str, **context):   # pragma: no cover - no fetching
        return []


# ===========================================================================
# 2026 editions. Dates verified against the linked official pages on the
# date noted per block — refresh next season (finished entries stop
# yielding on their own; replace them when the next year is announced).
# ===========================================================================

# Dates verified 2026-07-27 against the linked official pages (research
# pass). NOT included, with reasons: 戸田橋花火大会 (the Saitama bank of
# the same Arakawa show as いたばし — no direct official confirmation;
# Itabashi covers it for our readers), 神田古本まつり (jimbou.info has not
# published 2026 dates yet — add when announced, pattern: last Tue of Oct
# through Nov 3), 隅田川/立川昭和記念公園 (2026 editions passed Jul 25 —
# next season). Yokohama Night Flowers gains extra Oct+ dates in August
# per its official site — refresh then.

MATSURI_EDITIONS: tuple[SeasonalEdition, ...] = (
    SeasonalEdition(
        key="fukagawa_hachiman", title_ja="深川八幡祭り（富岡八幡宮例大祭・本祭）",
        title_en="Fukagawa Hachiman Matsuri (main festival year)",
        category=Category.FESTIVAL, dates=("2026-08-12", "2026-08-16"),
        venue_area="Tomioka Hachimangu, Koto-ku",
        url="https://www.baynet.ne.jp/fukagawamatsuri/"),
    SeasonalEdition(
        key="azabujuban_matsuri", title_ja="麻布十番納涼まつり",
        title_en="Azabu-Juban Noryo Matsuri",
        category=Category.FESTIVAL, dates=("2026-08-22", "2026-08-23"),
        venue_area="Azabu-Juban, Minato-ku",
        url="https://www.azabujuban.or.jp/topics/topics_event/23867/"),
    SeasonalEdition(
        key="otsuka_awaodori", title_ja="東京大塚阿波おどり",
        title_en="Tokyo Otsuka Awa Odori",
        category=Category.FESTIVAL, dates=("2026-08-28", "2026-08-29"),
        venue_area="Minami-Otsuka, Toshima-ku", start_time="16:00",
        url="https://www.city.toshima.lg.jp/ike-circle/tourism/event/"
            "awaodori.html"),
    SeasonalEdition(
        key="koenji_awaodori", title_ja="東京高円寺阿波おどり",
        title_en="Tokyo Koenji Awa Odori",
        category=Category.FESTIVAL, dates=("2026-08-29", "2026-08-30"),
        venue_area="Koenji, Suginami-ku", start_time="17:00",
        url="https://www.koenji-awaodori.com/"),
    SeasonalEdition(
        key="super_yosakoi", title_ja="原宿表参道元氣祭スーパーよさこい",
        title_en="Harajuku Omotesando Genki Matsuri Super Yosakoi",
        category=Category.FESTIVAL, dates=("2026-08-29", "2026-08-30"),
        venue_area="Harajuku / Omotesando / Yoyogi Park, Shibuya-ku",
        url="https://www.super-yosakoi.tokyo/"),
    SeasonalEdition(
        key="asakusa_samba", title_ja="浅草サンバカーニバル パレードコンテスト",
        title_en="Asakusa Samba Carnival",
        category=Category.FESTIVAL, dates=("2026-08-29",),
        venue_area="Kaminarimon-dori, Asakusa, Taito-ku",
        url="https://www.asakusa-samba.org/"),
    SeasonalEdition(
        key="fukuro_matsuri", title_ja="ふくろ祭り（御輿の祭典）",
        title_en="Fukuro Matsuri (mikoshi festival)",
        category=Category.FESTIVAL, dates=("2026-09-26", "2026-09-27"),
        venue_area="Ikebukuro West Exit, Toshima-ku",
        url="https://www.city.toshima.lg.jp/ike-circle/tourism/event/"
            "fukuromaturi.html"),
    SeasonalEdition(
        key="tokyo_yosakoi", title_ja="ふくろ祭り・東京よさこい（踊りの祭典）",
        title_en="Tokyo Yosakoi",
        category=Category.FESTIVAL, dates=("2026-10-10", "2026-10-11"),
        venue_area="Ikebukuro, Toshima-ku",
        url="https://tokyo-yosakoi.jp/"),
    SeasonalEdition(
        key="kawagoe_matsuri", title_ja="川越まつり",
        title_en="Kawagoe Matsuri (UNESCO float festival)",
        category=Category.FESTIVAL, dates=("2026-10-17", "2026-10-18"),
        venue_area="Kawagoe, Saitama",
        url="https://koedo.or.jp/event/%E5%B7%9D%E8%B6%8A%E3%81%BE%E3%81%A4"
            "%E3%82%8A-4/"),
    SeasonalEdition(
        key="tori_no_ichi", title_ja="酉の市（浅草 鷲神社・長國寺）",
        title_en="Tori-no-Ichi fair (Asakusa Otori Shrine)",
        category=Category.FESTIVAL, dates=("2026-11-07", "2026-11-19"),
        contiguous=False,        # 一の酉 / 二の酉 zodiac days; no 三の酉 in 2026
        venue_area="Senzoku, Taito-ku",
        url="https://torinoichi.jp/"),
    SeasonalEdition(
        key="chichibu_yomatsuri", title_ja="秩父夜祭",
        title_en="Chichibu Night Festival (UNESCO)",
        category=Category.FESTIVAL, dates=("2026-12-02", "2026-12-03"),
        venue_area="Chichibu Shrine, Chichibu, Saitama",
        url="https://www.chichibu-jinja.or.jp/"),
)

HANABI_EDITIONS: tuple[SeasonalEdition, ...] = (
    SeasonalEdition(
        key="edogawa_hanabi", title_ja="江戸川区花火大会",
        title_en="Edogawa Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-08-01",),
        venue_area="Edogawa riverbed, Edogawa-ku", start_time="19:15",
        url="https://www.city.edogawa.tokyo.jp/e004/kuseijoho/kohokocho/"
            "press/2026/04/0408.html"),
    SeasonalEdition(
        key="itabashi_hanabi", title_ja="いたばし花火大会",
        title_en="Itabashi Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-08-01",),
        venue_area="Arakawa riverbed, Itabashi-ku", start_time="19:00",
        url="https://itabashihanabi.jp/"),
    SeasonalEdition(
        key="hachioji_hanabi", title_ja="八王子花火大会",
        title_en="Hachioji Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-08-01",),
        venue_area="Fujimori Park, Hachioji", start_time="19:00",
        url="https://www.city.hachioji.tokyo.jp/kankobunka/001/001/"
            "p003249.html"),
    SeasonalEdition(
        key="makuhari_beach_hanabi", title_ja="幕張ビーチ花火フェスタ",
        title_en="Makuhari Beach Fireworks Festa",
        category=Category.FIREWORKS, dates=("2026-08-01",),
        venue_area="Makuhari Seaside Park, Chiba", start_time="19:30",
        url="https://chiba-hanabi.jp/"),
    SeasonalEdition(
        key="jingu_gaien_hanabi", title_ja="神宮外苑花火大会",
        title_en="Jingu Gaien Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-08-08",),
        venue_area="Meiji Jingu Gaien, Shinjuku-ku", start_time="19:30",
        url="https://www.jinguhanabi.com/"),
    SeasonalEdition(
        key="yokohama_night_flowers", title_ja="ヨコハマナイトフラワーズ",
        title_en="Yokohama Night Flowers",
        category=Category.FIREWORKS,
        dates=("2026-08-09", "2026-09-05", "2026-09-20"),
        contiguous=False,        # 5-minute short-burst series; Oct+ dates TBA
        venue_area="Shinko Wharf / Osanbashi, Yokohama",
        url="https://www.yokohama-nightflowers.com/"),
    SeasonalEdition(
        key="minatomirai_festival", title_ja="みなとみらいフェスティバル",
        title_en="Minato Mirai Festival (fireworks)",
        category=Category.FIREWORKS, dates=("2026-08-24",),
        venue_area="Minato Mirai 21, Yokohama", start_time="18:00",
        url="https://www.mmsf.yokohama/"),
    SeasonalEdition(
        key="chofu_hanabi", title_ja="映画のまち調布花火",
        title_en="Chofu Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-09-12",),
        venue_area="Tamagawa riverside, Chofu", start_time="18:15",
        url="https://hanabi.csa.gr.jp/"),
    SeasonalEdition(
        key="setagaya_tamagawa_hanabi", title_ja="世田谷区たまがわ花火大会",
        title_en="Setagaya Tamagawa Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-10-03",),
        venue_area="Futako-Tamagawa, Setagaya-ku", start_time="18:00",
        url="https://tamagawa-hanabi.com/"),
    SeasonalEdition(
        key="kawasaki_tamagawa_hanabi", title_ja="川崎市制記念多摩川花火大会",
        title_en="Kawasaki Tamagawa Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-10-03",),
        venue_area="Tamagawa riverside, Takatsu-ku, Kawasaki",
        start_time="18:00",
        url="https://www.city.kawasaki.jp/280/page/0000117559.html"),
    SeasonalEdition(
        key="konosu_hanabi", title_ja="こうのす花火大会",
        title_en="Konosu Fireworks Festival",
        category=Category.FIREWORKS, dates=("2026-10-10",),
        venue_area="Arakawa riverbed, Konosu, Saitama", start_time="17:30",
        url="https://kounosuhanabi.com/"),
    SeasonalEdition(
        key="atsugi_ayu_hanabi", title_ja="あつぎ鮎まつり大花火大会",
        title_en="Atsugi Ayu Matsuri Fireworks",
        category=Category.FIREWORKS, dates=("2026-10-10",),
        venue_area="Sagami River, Atsugi, Kanagawa",   # moved Aug->Oct in 2026
        url="https://www.city.atsugi.kanagawa.jp/soshiki/shogyonigiwaika/"
            "7_1/5/43235.html"),
)
