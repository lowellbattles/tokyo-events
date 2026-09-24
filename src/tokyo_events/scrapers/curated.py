"""Hand-entered concert listings (source "curated_concerts", 2026-09-24).

For shows we can't scrape: the official site blocks our honest bot (rule 2
— Suntory Hall's suntory.co.jp 403s every path, robots.txt included) and
no scrapable promoter carries the show (Suntory Hall's own 主催公演).

How it works — the same model as the festival/matsuri configs:
- CURATED_CONCERTS below IS the fact base. Each entry is typed in by a
  person after reading the OFFICIAL page in a normal browser, and records
  that page's URL (source_url, the site's "公演ページ →" link) and the day
  it was checked (`verified`). Facts only: title, dates, times, venue,
  prices, performers, sold-out, sale windows — never descriptions or
  program notes.
- The scraper fetches nothing. It yields every entry date that hasn't
  passed yet (finished shows sunset on their own) and reports found=0
  without tripping the loud-zero guard (allow_empty).
- `venue` is the RAW hall string; it resolves via venues.py at export
  like any promoter row (this source is in promoters.PROMOTER_SOURCES),
  so if a scraped source later lists the same show the two fold into one.
- Curated facts go stale: sold-out, times, even dates can change after
  entry. Re-check an entry's official page when touching this file and
  bump `verified`; when the site or a promoter becomes scrapable, prefer
  that and delete the entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Category, Event
from . import textutils as tu
from .base import BaseScraper


@dataclass(frozen=True)
class CuratedConcert:
    title_ja: str
    #: (show date ISO, official page URL for that date)
    dates: tuple[tuple[str, str], ...]
    venue: str                            # raw; venues.resolve_venue at export
    verified: str                         # ISO day the official page was read
    title_en: Optional[str] = None
    open_time: Optional[str] = None
    start_time: Optional[str] = None
    price_text: Optional[str] = None
    price_min: Optional[int] = None
    lineup: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    #: show dates the official page marks sold out (予定枚数終了 / 完売)
    sold_out: tuple[str, ...] = ()
    #: (label, opens, closes) as ISO date/datetime — years ARE printed on
    #: official pages, so no inference here
    sales: tuple[tuple[str, str, Optional[str]], ...] = field(default=())


CURATED_CONCERTS: tuple[CuratedConcert, ...] = (
    # Suntory Hall 40th-anniversary 主催公演. Both nights: 大ホール, doors
    # 18:20 / 19:00. Official: detail/20261025_M_3.html + 20261027_M_3.html
    # (series pages) and news release sh0495. Both pages show 一般発売
    # "予定枚数終了" (allocation exhausted) as of 2026-09-24.
    CuratedConcert(
        title_ja="ヨーヨー・マ サントリーホール40周年スペシャルコンサート",
        title_en="Yo-Yo Ma — Suntory Hall 40th Anniversary Special Concert",
        dates=(
            ("2026-10-25", "https://www.suntory.co.jp/suntoryhall/schedule/"
                           "detail/20261025_M_3.html"),
            ("2026-10-27", "https://www.suntory.co.jp/suntoryhall/schedule/"
                           "detail/20261027_M_3.html"),
        ),
        venue="サントリーホール 大ホール",
        verified="2026-09-24",
        open_time="18:20", start_time="19:00",
        price_text="S席27,000 A席22,000 B席17,000 C席12,000 U25席3,000",
        # cheapest tier open to everyone: age-restricted U25/学生 seats
        # would read as a misleading "¥3,000〜" for most visitors
        price_min=12000,
        lineup=("ヨーヨー・マ", "若尾圭良", "ユン・ジャニス・ルー",
                "ジェレミー・ダッチャー", "OKI", "MAREWREW"),
        genres=("classical",),
        sold_out=("2026-10-25", "2026-10-27"),
        sales=(
            ("サントリーホール・メンバーズ・クラブ先行発売",
             "2026-07-18T10:00", "2026-07-24T23:59"),
            ("一般発売", "2026-07-25T10:00", None),
        ),
    ),
)


class CuratedConcertsScraper(BaseScraper):
    source_id = "curated_concerts"
    source_name = "Curated concerts (hand-entered)"
    supports_detail = False
    allow_empty = True            # an empty list between entries is normal

    def __init__(self, entries: tuple[CuratedConcert, ...] = CURATED_CONCERTS,
                 **kw):
        super().__init__(**kw)
        self.entries = entries

    def scrape(self, today: Optional[str] = None) -> Iterable[Event]:
        today = today or tu.jst_today().isoformat()
        for c in self.entries:
            sales = [{"kind": tu.sale_kind(label), "label": label,
                      "opens": opens, "closes": closes}
                     for label, opens, closes in c.sales]
            for date, url in c.dates:
                if date < today:
                    continue                      # finished night: sunset
                yield Event(
                    source=self.source_id, source_url=url,
                    title_ja=c.title_ja, title_en=c.title_en,
                    category=Category.MUSIC, genres=list(c.genres),
                    start_date=date,
                    open_time=c.open_time, start_time=c.start_time,
                    venue_name=c.venue,
                    price_text=c.price_text, price_min=c.price_min,
                    is_sold_out=date in c.sold_out,
                    lineup=list(c.lineup), sales=list(sales),
                )

    def parse(self, html: str, **context):   # pragma: no cover - no fetching
        return []
