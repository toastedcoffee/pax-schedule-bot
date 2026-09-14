import sqlite3
from datetime import UTC, date, datetime

import pytest

from paxbot.models import Event
from paxbot.store.db import SCHEMA_VERSION, SchemaVersionError, TransactionError, connect, transaction
from paxbot.store.events import (
    event_count,
    events_for_day,
    get_event,
    mark_cancelled,
    stored_hashes,
    upsert_events,
)


def make_event(gt_id="1", title="Panel", hour=18, categories=("Panels",), day=None):
    starts = datetime(2026, 9, 4, hour, 0, tzinfo=UTC)
    return Event(
        show_slug="west",
        gt_id=gt_id,
        title=title,
        description="d",
        starts_at=starts,
        ends_at=starts.replace(hour=hour + 1),
        day=day or date(2026, 9, 4),
        location="Room A",
        url="https://example.invalid/x",
        categories=categories,
        row_hash=f"hash-{gt_id}-{title}",
    )


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def test_upsert_then_read_back(conn):
    with transaction(conn):
        upsert_events(conn, [make_event()])
    got = get_event(conn, "west", "1")
    assert got.title == "Panel"
    assert got.starts_at == datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    assert got.categories == ("Panels",)


def test_upsert_is_idempotent_and_updates(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(title="Old")])
    with transaction(conn):
        upsert_events(conn, [make_event(title="New")])
    assert event_count(conn, "west") == 1
    assert get_event(conn, "west", "1").title == "New"


def test_categories_are_replaced_not_appended(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(categories=("A", "B"))])
    with transaction(conn):
        upsert_events(conn, [make_event(categories=("C",))])
    assert get_event(conn, "west", "1").categories == ("C",)


def test_stored_hashes_maps_id_to_hash(conn):
    with transaction(conn):
        upsert_events(conn, [make_event("1"), make_event("2")])
    assert stored_hashes(conn, "west") == {
        "1": "hash-1-Panel",
        "2": "hash-2-Panel",
    }


def test_cancelled_events_are_retained_but_excluded(conn):
    with transaction(conn):
        upsert_events(conn, [make_event("1"), make_event("2")])
    with transaction(conn):
        mark_cancelled(conn, "west", ["2"])
    assert event_count(conn, "west") == 1
    assert stored_hashes(conn, "west") == {"1": "hash-1-Panel"}
    assert get_event(conn, "west", "2") is not None  # retained, not deleted


def test_events_for_day_filters_and_orders(conn):
    with transaction(conn):
        upsert_events(conn, [
            make_event("1", hour=20),
            make_event("2", hour=18),
            make_event("3", hour=19, day=date(2026, 9, 5)),
        ])
    got = events_for_day(conn, "west", date(2026, 9, 4))
    assert [e.gt_id for e in got] == ["2", "1"]


def test_events_for_day_tiebreaks_on_gt_id_when_start_and_title_match(conn):
    """ORDER BY starts_at, title alone leaves ties non-deterministic."""
    with transaction(conn):
        upsert_events(conn, [
            make_event("9", title="Same", hour=18),
            make_event("2", title="Same", hour=18),
            make_event("5", title="Same", hour=18),
        ])
    got = events_for_day(conn, "west", date(2026, 9, 4))
    assert [e.gt_id for e in got] == ["2", "5", "9"]


def test_categories_round_trip_in_their_original_order(conn):
    """SQLite's BINARY collation would otherwise put 'TTRPGs' before 'Tabletop'."""
    with transaction(conn):
        upsert_events(conn, [make_event(categories=("Age 13+", "Tabletop", "TTRPGs"))])
    assert get_event(conn, "west", "1").categories == ("Age 13+", "Tabletop", "TTRPGs")


def test_events_for_day_filters_by_category(conn):
    with transaction(conn):
        upsert_events(conn, [
            make_event("1", categories=("Panels",)),
            make_event("2", categories=("Tabletop",)),
        ])
    got = events_for_day(conn, "west", date(2026, 9, 4), category="Tabletop")
    assert [e.gt_id for e in got] == ["2"]


def test_events_for_day_category_filter_is_case_insensitive(conn):
    """A user typing "tabletop" for the stored "Tabletop" should match - a
    plain SQLite `=` on TEXT is case-sensitive and silently returns nothing."""
    with transaction(conn):
        upsert_events(conn, [make_event("1", categories=("Tabletop",))])
    for query in ("tabletop", "TABLETOP", "TaBlEtOp"):
        got = events_for_day(conn, "west", date(2026, 9, 4), category=query)
        assert [e.gt_id for e in got] == ["1"], f"category={query!r} did not match"


def test_nested_transaction_is_refused(conn):
    """Nesting would let an inner block commit work the outer means to undo."""
    with pytest.raises(TransactionError):
        with transaction(conn):
            with transaction(conn):
                pass


def test_transaction_rolls_back_on_error(conn):
    with transaction(conn):
        upsert_events(conn, [make_event("1")])
    with pytest.raises(RuntimeError):
        with transaction(conn):
            upsert_events(conn, [make_event("2")])
            raise RuntimeError("boom")
    assert event_count(conn, "west") == 1


def test_fresh_database_is_stamped_with_the_current_schema_version(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_a_database_from_a_newer_paxbot_is_refused(tmp_path):
    """An existing DB at a schema version this code doesn't understand must
    fail loudly rather than silently operate on a shape it doesn't know."""
    path = tmp_path / "future.db"
    conn = connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(SchemaVersionError):
        connect(path)


PRE_ORDINAL_SCHEMA = """
CREATE TABLE events (show_slug TEXT, gt_id TEXT, title TEXT, description TEXT,
  day TEXT, starts_at TEXT, ends_at TEXT, location TEXT, url TEXT,
  cancelled INTEGER DEFAULT 0, row_hash TEXT, updated_at TEXT,
  PRIMARY KEY (show_slug, gt_id));
CREATE TABLE event_categories (show_slug TEXT, gt_id TEXT, category TEXT,
  PRIMARY KEY (show_slug, gt_id, category));
CREATE TABLE sync_runs (show_slug TEXT, ran_at TEXT, event_count INTEGER,
  ok INTEGER, note TEXT DEFAULT '');
INSERT INTO events VALUES ('west','1','Old Event','d','2026-09-04',
  '2026-09-04T18:00:00+00:00','2026-09-04T19:00:00+00:00','R','u',0,'h','t');
INSERT INTO event_categories VALUES ('west','1','Panels');
"""


def test_a_pre_ordinal_database_is_migrated_not_just_stamped(tmp_path):
    """The hook must ALTER before it stamps, or the database is permanently
    broken with no path to recovery."""
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(PRE_ORDINAL_SCHEMA)
    legacy.commit()
    legacy.close()

    conn = connect(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(event_categories)")}
    assert "ordinal" in columns
    # Was hardcoded `== 1`; that stamped-version literal predates schema v2
    # and this line, uniquely in this file, never referenced the SCHEMA_VERSION
    # symbol its sibling assertion above (line 149) already uses. The test's
    # intent - "stamped to the current version" - is unchanged.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # and the pre-existing data is still queryable
    assert [e.gt_id for e in events_for_day(conn, "west", date(2026, 9, 4))] == ["1"]
    conn.close()


def test_a_v1_database_missing_ordinal_is_repaired_not_just_stamped(tmp_path):
    """The v1->v2 bump made this reachable: a v1 database still falls through
    to the ordinal repair, and gating that repair on `current < 1` skipped
    exactly the databases that needed it."""
    path = tmp_path / "v1.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(PRE_ORDINAL_SCHEMA + "PRAGMA user_version = 1;")
    raw.commit()
    raw.close()

    conn = connect(path)
    try:
        columns = {r["name"] for r in conn.execute(
            "PRAGMA table_info(event_categories)")}
        assert "ordinal" in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # The real symptom: category reads must work afterwards.
        assert events_for_day(conn, "west", date(2026, 9, 4)) is not None
    finally:
        conn.close()


def test_migrating_preserves_existing_rows(tmp_path):
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(PRE_ORDINAL_SCHEMA)
    legacy.commit()
    legacy.close()

    conn = connect(path)
    assert get_event(conn, "west", "1").categories == ("Panels",)
    conn.close()
