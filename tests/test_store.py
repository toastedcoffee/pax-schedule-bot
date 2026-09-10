from datetime import UTC, date, datetime

import pytest

from paxbot.models import Event
from paxbot.store.db import connect, transaction
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


def test_events_for_day_filters_by_category(conn):
    with transaction(conn):
        upsert_events(conn, [
            make_event("1", categories=("Panels",)),
            make_event("2", categories=("Tabletop",)),
        ])
    got = events_for_day(conn, "west", date(2026, 9, 4), category="Tabletop")
    assert [e.gt_id for e in got] == ["2"]


def test_transaction_rolls_back_on_error(conn):
    with transaction(conn):
        upsert_events(conn, [make_event("1")])
    with pytest.raises(RuntimeError):
        with transaction(conn):
            upsert_events(conn, [make_event("2")])
            raise RuntimeError("boom")
    assert event_count(conn, "west") == 1
