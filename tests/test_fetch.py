"""fetch() error typing + retry discipline, and month-walk termination
semantics (roadmap R7 / register SCR-4).

The rules under test:
- 404/410 -> NotFoundError, immediately (the normal end-of-calendar
  signal month walkers stop on);
- other 4xx -> FetchError, immediately (an answer, not an outage — no
  retry, and a partial WAF block must surface loud);
- 5xx / network trouble -> retried, then FetchError;
- both types subclass RuntimeError so legacy callers keep working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
import requests  # noqa: E402

from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402
from tokyo_events.scrapers import base as base_mod  # noqa: E402
from tokyo_events.scrapers.base import (BaseScraper, FetchError,  # noqa: E402
                                        NotFoundError)


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.content = b"<html>ok</html>"
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"
        self.text = "<html>ok</html>"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}",
                                     response=self)


class _Session:
    """Serves the scripted status list, then repeats the last entry."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0
        self.headers = {}

    def get(self, url, timeout):
        s = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return _Resp(s)


class _Dummy(BaseScraper):
    source_id = "dummy"

    def scrape(self):
        return []

    def parse(self, html, **context):
        return []


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)


def test_403_raises_fetcherror_immediately_without_retry():
    s = _Session([403])
    with pytest.raises(FetchError) as ei:
        _Dummy(session=s).fetch("https://x/blocked")
    assert not isinstance(ei.value, NotFoundError)
    assert s.calls == 1


def test_404_raises_notfound_immediately_without_retry():
    s = _Session([404])
    with pytest.raises(NotFoundError):
        _Dummy(session=s).fetch("https://x/gone")
    assert s.calls == 1


def test_5xx_retries_then_fails_loud():
    s = _Session([500])
    with pytest.raises(FetchError):
        _Dummy(session=s).fetch("https://x/broken")
    assert s.calls == 3                     # 1 try + 2 retries


def test_5xx_then_ok_recovers():
    s = _Session([502, 200])
    assert "ok" in _Dummy(session=s).fetch("https://x/heals")
    assert s.calls == 2


def test_typed_errors_remain_runtimeerror_for_legacy_callers():
    assert issubclass(FetchError, RuntimeError)
    assert issubclass(NotFoundError, FetchError)


# ---- month-walk termination through the pipeline --------------------------

class _Walker(BaseScraper):
    """Three-month walker in the house pattern: break on NotFoundError,
    let anything else propagate."""

    source_id = "dummy"
    source_name = "walker"
    supports_detail = False
    month2_error: type = NotFoundError

    def fetch(self, url, retries=2):
        if url.endswith("m2"):
            raise self.month2_error(f"[dummy] {url}: month 2 unavailable")
        return "<html>ok</html>"

    def scrape(self):
        for i in (1, 2, 3):
            try:
                html = self.fetch(f"https://x/m{i}")
            except NotFoundError:
                break                       # end of the published runway
            yield from self.parse(html, month=i)

    def parse(self, html, month=0, **context):
        return [Event(source="dummy", source_url=f"https://x/ev{month}",
                      title_ja=f"show {month}",
                      start_date=f"2099-01-{month:02d}",
                      category=Category.MUSIC)]


def test_month_walk_404_is_a_clean_stop(tmp_path, monkeypatch):
    from tokyo_events import pipeline
    from tokyo_events.db import EventStore

    class W(_Walker):
        month2_error = NotFoundError

    store = EventStore(tmp_path / "w.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (W, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["error"] is None
    assert report["found"] == 1             # month 1 landed, walk stopped


def test_month_walk_403_fails_loud_with_partial_data(tmp_path, monkeypatch):
    from tokyo_events import pipeline
    from tokyo_events.db import EventStore

    class W(_Walker):
        month2_error = FetchError

    store = EventStore(tmp_path / "w.db")
    monkeypatch.setattr(pipeline, "SCRAPERS",
                        {"dummy": (W, ReviewStatus.PENDING)})
    report = pipeline.run(store, only=["dummy"], fetch_details=False)[0]
    assert report["error"] is not None      # a partial block stays LOUD
    assert "FetchError" in report["error"]
    assert report["found"] == 1             # month 1's data still landed
