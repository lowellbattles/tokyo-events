"""Canonical event model.

Every scraper, regardless of source, normalizes into this shape.
Design principles:
- Store FACTS (title, date, venue, price) — link out to the source for
  descriptions/images rather than copying copyrighted content.
- Bilingual-ready: ja/en fields are separate; en may be machine-translated
  later and flagged as such.
- Dates are ISO strings (YYYY-MM-DD); times are HH:MM strings (JST implied).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Category(str, Enum):
    MUSIC = "music"
    ART = "art"
    FESTIVAL = "festival"        # matsuri
    MUSIC_FESTIVAL = "music_festival"  # Fuji Rock, Summer Sonic, ...
    FIREWORKS = "fireworks"      # hanabi
    FLOWERS = "flowers"          # sakura, ajisai, etc.
    FOOD = "food"
    OTHER = "other"


class ReviewStatus(str, Enum):
    PENDING = "pending"          # scraped, awaiting human review
    APPROVED = "approved"        # visible to users
    REJECTED = "rejected"        # hidden (spam, private booking, etc.)
    AUTO = "auto"                # auto-published (trusted source)


# Genre facets for Category.MUSIC (see roadmap discussion).
GENRES = [
    "idol", "j-rock", "international", "k-pop", "jazz-soul",
    "classical", "hiphop-rnb", "electronic", "anime-seiyu",
]


@dataclass
class Event:
    # --- identity ---
    source: str                  # scraper id, e.g. "liquidroom"
    source_url: str              # canonical detail page on the source site

    # --- core facts ---
    title_ja: Optional[str] = None
    title_en: Optional[str] = None
    subtitle: Optional[str] = None      # tour name / exhibition subtitle
    category: Category = Category.OTHER
    genres: list[str] = field(default_factory=list)

    start_date: Optional[str] = None    # YYYY-MM-DD
    end_date: Optional[str] = None      # for multi-day events/exhibitions
    open_time: Optional[str] = None     # HH:MM
    start_time: Optional[str] = None    # HH:MM

    # --- venue ---
    venue_name: Optional[str] = None
    venue_area: Optional[str] = None    # e.g. "Ebisu", "Shibuya"
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

    # --- ticketing ---
    price_text: Optional[str] = None    # raw price string (often complex tiers)
    price_min: Optional[int] = None     # JPY, parsed lowest tier if possible
    is_free: Optional[bool] = None
    is_sold_out: bool = False
    ticket_url: Optional[str] = None
    #: [{"provider": "eplus"|"pia"|"lawson"|..., "url": str, "code": str|None}]
    ticket_links: list[dict] = field(default_factory=list)

    # --- extras (facts only) ---
    lineup: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def dedupe_key(self) -> str:
        """Stable identity: same source + same detail URL = same event."""
        return hashlib.sha256(
            f"{self.source}|{self.source_url}".encode()
        ).hexdigest()[:16]

    def content_hash(self) -> str:
        """Changes when any scraped field changes -> triggers re-review."""
        d = asdict(self)
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16]

    def to_json(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        return d
