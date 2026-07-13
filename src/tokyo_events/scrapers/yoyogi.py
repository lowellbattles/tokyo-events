"""Scraper for 国立代々木競技場 第一体育館 (Yoyogi National Gymnasium,
First Gymnasium) — operated by JAPAN SPORT COUNCIL (日本スポーツ振興センター),
https://www.jpnsport.go.jp/yoyogi/

Static, server-rendered DotNetNuke (DNN) page. The whole schedule lives at
ONE URL (event/tabid/59/default.aspx) — there is NO ?ym=/month pagination.
That single page carries three server-rendered sections inline:
  当月のイベント (present month), 来月のイベント (next month),
  再来月以降のイベント (month-after-next onward).
The site explicitly declares it does not publish beyond the month after
next ("再来月以降のイベント情報は掲載しておりません。"), so a daily re-fetch
of this one URL is the only — and complete — way to catch events as
organizers announce them. scrape() therefore does one GET and delegates.

The page renders the event table TWICE (main content + a "月間予定表"
sidebar copy) with identical rows; dedupe by source_url collapses them.

Listing row (keys off TEXT + URL shape, not the churning numeric DNN
module id `ctr1044`):
    <tr>
      <td>2026/07/15(水)</td>
      <td><a href=".../Default.aspx?TabId=59&eid=6004&etype=1">TITLE</a></td>
    </tr>
Each listed day is its OWN eid (a multi-day run gets one row + one detail
page per date), so every row is a distinct event with a distinct
source_url — no #date-fragment grouping is needed here.

Detail page (custom parse_detail): a label/value table using the venue's
Japanese labels 開場時間 (open) / 開始時間 (start) — NOT the English
OPEN/START the generic base.parse_detail keys off — plus お問合わせ先, which
usually carries the promoter's 特設サイト link (stored as ticket_url). There
is NO price anywhere on the JSC site (it is the facility operator, not the
promoter), so price_min stays None and the pipeline keeps this source in
its detail backlog — bounded per run by the politeness cap.

MIXED CALENDAR — category policy is load-bearing here. This is a national
sports facility: most bookings are sports (karate, wrestling and other
選手権 national championships), with concerts/fan-meetings the minority.
The site publishes no category tag. Non-concert rows are kept (facts-only)
but tagged Category.OTHER via tu.is_nonmusic PLUS the venue's own
competition vocabulary (VENUE_OTHER_RE) — every token there is a
competitive-sport term that is never a concert title, so the bias is
toward OTHER (avoid polluting the music feed) rather than toward MUSIC.
This mirrors the ariake_arena / garden_theater precedent; it is NOT an
invented broad keyword list.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Category, Event
from .base import BaseScraper
from . import textutils as tu

# One config per JSC/Yoyogi hall. Only 第一体育館 (tabid 59) is in scope now;
# the Second Gymnasium would be a sibling tabid on the same DNN structure.
HALLS = {
    "yoyogi_gym1": dict(
        tabid=59,
        schedule_url=(
            "https://www.jpnsport.go.jp/yoyogi/event/tabid/59/default.aspx"),
        venue_name="国立代々木競技場 第一体育館",
        venue_area="Harajuku",
        address="2-1-1 Jinnan, Shibuya-ku, Tokyo",
        lat=35.6702,
        lng=139.6967,
    ),
}

TIME_RE = re.compile(r"(\d{1,2}:\d{2})")

# The JSC gymnasium's schedule is sports-dominant. tu.is_nonmusic already
# covers 大相撲/プロレス/バレーボール/バスケットボール/世界選手権 etc., but the
# tournaments this facility actually hosts (karate, wrestling, and other
# 大会/選手権 national championships) use vocabulary the shared helper does
# not carry. Every token below is a competitive-sport / tournament term that
# is never a concert title — the venue's own non-concert vocabulary, kept
# tight on purpose (precision toward OTHER, since polluting the music feed
# with sports is the failure mode called out for this venue).
VENUE_OTHER_RE = re.compile(
    r"選手権|競技会|記録会|"
    r"カラテ|空手|レスリング|柔道|剣道|なぎなた|テコンドー|"
    r"体操|新体操|フェンシング|ウエイトリフティング|アーチェリー|"
    r"少年少女|大会",
    re.I,
)


class YoyogiScraper(BaseScraper):
    source_name = "国立代々木競技場"
    BASE = "https://www.jpnsport.go.jp"

    def __init__(self, hall_id: str = "yoyogi_gym1", **kw):
        super().__init__(**kw)
        if hall_id not in HALLS:
            raise ValueError(f"unknown Yoyogi hall: {hall_id}")
        self.hall = HALLS[hall_id]
        self.source_id = hall_id
        self._tabid = self.hall["tabid"]
        # Event-detail anchors carry ?...&eid=NNN; require the matching
        # TabId so a future gym2 link on a shared page can't be grabbed.
        self._eid_re = re.compile(r"[?&]eid=(\d+)", re.I)
        self._tabid_re = re.compile(rf"[?&]tabid={self._tabid}(?:&|$)", re.I)

    # ------------------------------------------------------------------ fetch
    def scrape(self) -> Iterable[Event]:
        """One static page holds every listed month — no pagination."""
        yield from self.parse(self.fetch(self.hall["schedule_url"]))

    # -- pure parse (html in, Events out); today= only feeds year fallback --
    def parse(self, html: str, today: dt.date | None = None,
              **context) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        events: dict[str, Event] = {}
        # All three month sections render as <table class="event-calendar">
        # (present / next / since-month-after-next), plus a duplicate set in
        # the sidebar. Iterate every row; empty months are a single
        # NotFoundData <tr> with no qualifying anchor and are skipped.
        for tr in soup.select("table.event-calendar tr"):
            a = self._event_anchor(tr)
            if a is None:
                continue
            href = a["href"]
            url = urljoin(self.hall["schedule_url"], href)
            date = tu.parse_date(tr.get_text(" ", strip=True), today)
            if not date:
                continue                    # loud per row: no parseable date
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            if not title:
                continue
            ev = Event(
                source=self.source_id, source_url=url,
                title_ja=title, category=self._classify(title), genres=[],
                start_date=date,
                venue_name=self.hall["venue_name"],
                venue_area=self.hall["venue_area"],
                address=self.hall["address"],
                lat=self.hall["lat"], lng=self.hall["lng"],
            )
            if ev.source_url not in events:
                events[ev.source_url] = ev
        return list(events.values())

    def _event_anchor(self, tr):
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if self._eid_re.search(href) and self._tabid_re.search(href):
                return a
        return None

    @staticmethod
    def _classify(title: str) -> Category:
        if tu.is_nonmusic(title) or VENUE_OTHER_RE.search(title):
            return Category.OTHER
        return Category.MUSIC

    # --------------------------------------------------------------- detail
    def parse_detail(self, html: str, ev: Event) -> Event:
        """Fill open/start times (labelled 開場時間 / 開始時間) and the
        promoter's 特設サイト link (お問合わせ先) from the event's own page.
        Keys off the visible Japanese <th> labels — robust to the numeric
        DNN module id changing between page regenerations."""
        soup = BeautifulSoup(html, "lxml")

        if not (ev.open_time or ev.start_time):
            ev.open_time = self._labeled_time(soup, "開場時間")
            ev.start_time = self._labeled_time(soup, "開始時間")

        if not ev.ticket_url:
            td = self._labeled_td(soup, "お問合わせ")
            if td is not None:
                link = td.find("a", href=True)
                if link and link["href"].startswith(("http://", "https://")):
                    ev.ticket_url = link["href"].strip()

        return ev

    @staticmethod
    def _labeled_td(soup, label: str):
        """The <td> paired with the first <th> whose text contains `label`."""
        for th in soup.find_all("th"):
            if label in th.get_text(" ", strip=True):
                tr = th.find_parent("tr")
                if tr is not None:
                    td = tr.find("td")
                    if td is not None:
                        return td
        return None

    @classmethod
    def _labeled_time(cls, soup, label: str) -> str | None:
        td = cls._labeled_td(soup, label)
        if td is None:
            return None
        m = TIME_RE.search(td.get_text(" ", strip=True))
        return m.group(1) if m else None
