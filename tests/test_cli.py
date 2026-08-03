"""CLI wiring (R18): loud --only validation and bulk pending approval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))        # repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402

import cli  # noqa: E402
from tokyo_events.db import EventStore  # noqa: E402
from tokyo_events.models import Category, Event, ReviewStatus  # noqa: E402


def test_unknown_only_source_errors_loudly(tmp_path, monkeypatch):
    # a typo'd --only used to run nothing and print nothing
    monkeypatch.setattr(sys, "argv",
                        ["cli.py", "--db", str(tmp_path / "x.db"),
                         "scrape", "--only", "zeppp_typo", "--no-details"])
    with pytest.raises(SystemExit):
        cli.main()


def test_bulk_approve_pending(tmp_path, monkeypatch, capsys):
    db = tmp_path / "x.db"
    store = EventStore(db)
    store.upsert(Event(source="liquidroom", source_url="https://x/1",
                       title_ja="A", category=Category.MUSIC,
                       start_date="2099-01-01"), ReviewStatus.PENDING)
    store.conn.close()
    monkeypatch.setattr(sys, "argv",
                        ["cli.py", "--db", str(db), "approve",
                         "--all-pending"])
    cli.main()
    assert "approved 1 pending" in capsys.readouterr().out
    assert EventStore(db).list_events(status="approved")


def test_approve_without_ids_or_flag_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["cli.py", "--db", str(tmp_path / "x.db"), "approve"])
    with pytest.raises(SystemExit):
        cli.main()
