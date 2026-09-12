import sqlite3
from datetime import date

import pytest

from paxbot.store.db import SCHEMA_VERSION, connect, transaction
from paxbot.store.events import mark_cancelled, upsert_events
from paxbot.store.saved import (
    missing_saved_ids,
    save_event,
    saved_events,
    saved_gt_ids,
    unsave_event,
)
from tests.conftest import make_event

USER = "123456789012345678"


def test_schema_version_is_two():
    assert SCHEMA_VERSION == 2


def test_save_then_read_back(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="7")])
    assert save_event(conn, USER, "west", "7") is True
    assert saved_gt_ids(conn, USER, "west") == frozenset({"7"})


def test_save_is_idempotent(conn):
    assert save_event(conn, USER, "west", "7") is True
    assert save_event(conn, USER, "west", "7") is False
    assert len(saved_gt_ids(conn, USER, "west")) == 1


def test_unsave_removes_and_reports(conn):
    save_event(conn, USER, "west", "7")
    assert unsave_event(conn, USER, "west", "7") is True
    assert unsave_event(conn, USER, "west", "7") is False
    assert saved_gt_ids(conn, USER, "west") == frozenset()


def test_saved_is_scoped_by_user_and_show(conn):
    save_event(conn, USER, "west", "7")
    save_event(conn, "999", "west", "8")
    save_event(conn, USER, "unplugged", "9")
    assert saved_gt_ids(conn, USER, "west") == frozenset({"7"})


def test_saved_events_are_ordered_by_start(conn):
    with transaction(conn):
        upsert_events(conn, [
            make_event(gt_id="late", hour=20),
            make_event(gt_id="early", hour=9),
        ])
    save_event(conn, USER, "west", "late")
    save_event(conn, USER, "west", "early")
    assert [e.gt_id for e in saved_events(conn, USER, "west")] == ["early", "late"]


def test_cancelled_events_are_still_returned(conn):
    """A cancelled event must stay visible on a saved schedule, marked, not dropped."""
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="7")])
        mark_cancelled(conn, "west", ["7"])
    save_event(conn, USER, "west", "7")
    assert [e.gt_id for e in saved_events(conn, USER, "west")] == ["7"]


def test_cancelled_flag_reaches_the_event_dataclass(conn):
    """Renderers need to tell a cancelled saved event from a live one."""
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="7")])
        mark_cancelled(conn, "west", ["7"])
    save_event(conn, USER, "west", "7")
    assert saved_events(conn, USER, "west")[0].cancelled is True


def test_events_default_to_not_cancelled(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="7")])
    save_event(conn, USER, "west", "7")
    assert saved_events(conn, USER, "west")[0].cancelled is False


def test_saved_id_with_no_event_row_is_reported_not_dropped(conn):
    save_event(conn, USER, "west", "ghost")
    assert saved_events(conn, USER, "west") == []
    assert missing_saved_ids(conn, USER, "west") == ("ghost",)


def test_no_foreign_key_constraint_on_saved(conn):
    """Deliberate: a saved row must outlive any surgery on the events table."""
    save_event(conn, USER, "west", "does-not-exist")
    assert saved_gt_ids(conn, USER, "west") == frozenset({"does-not-exist"})


def test_migrates_a_v1_database(tmp_path):
    # NOTE: deviates from the brief's literal fixture SQL. The brief's version
    # created `events` with only (show_slug, gt_id), which crashes inside
    # connect()'s `executescript(SCHEMA)` - unconditionally before _migrate()
    # runs - because SCHEMA's pre-existing `CREATE INDEX idx_events_day ON
    # events (show_slug, day, starts_at)` needs those columns to exist. This
    # is a bug in the brief's fixture, not in this task's code: the real v1
    # schema (already shipped) always had the full column set. The sibling
    # PRE_ORDINAL_SCHEMA fixture in tests/test_store.py already uses the full
    # column set for this exact reason. See task-1-report.md for detail.
    path = tmp_path / "v1.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        "CREATE TABLE events (show_slug TEXT, gt_id TEXT, title TEXT,"
        " description TEXT, day TEXT, starts_at TEXT, ends_at TEXT,"
        " location TEXT, url TEXT, cancelled INTEGER DEFAULT 0, row_hash TEXT,"
        " updated_at TEXT, PRIMARY KEY (show_slug, gt_id));"
        "CREATE TABLE event_categories (show_slug TEXT, gt_id TEXT, category TEXT,"
        " ordinal INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (show_slug, gt_id, category));"
        "PRAGMA user_version = 1;"
    )
    raw.close()

    c = connect(path)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "saved" in names
    finally:
        c.close()
