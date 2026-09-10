from datetime import UTC, date, datetime

import pytest

from paxbot.models import Event
from paxbot.sync.diff import GuardRailError, check_guard_rail, diff_events


def make_event(gt_id, row_hash):
    starts = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    return Event(
        show_slug="west", gt_id=gt_id, title="T", description="",
        starts_at=starts, ends_at=starts.replace(hour=19),
        day=date(2026, 9, 4), location="Room", url="u",
        categories=(), row_hash=row_hash,
    )


def test_new_event_is_added():
    d = diff_events({}, [make_event("1", "h1")])
    assert [e.gt_id for e in d.added] == ["1"]
    assert d.changed == [] and d.removed == []


def test_same_hash_is_unchanged():
    d = diff_events({"1": "h1"}, [make_event("1", "h1")])
    assert d.added == [] and d.changed == [] and d.removed == []
    assert d.unchanged == 1


def test_different_hash_is_changed():
    d = diff_events({"1": "h1"}, [make_event("1", "h2")])
    assert [e.gt_id for e in d.changed] == ["1"]


def test_missing_from_payload_is_removed():
    d = diff_events({"1": "h1", "2": "h2"}, [make_event("1", "h1")])
    assert d.removed == ["2"]


def test_guard_rail_allows_a_normal_sync():
    check_guard_rail(fetched_count=713, last_good_count=710)


def test_guard_rail_allows_growth():
    check_guard_rail(fetched_count=900, last_good_count=100)


def test_guard_rail_allows_first_ever_sync():
    check_guard_rail(fetched_count=713, last_good_count=0)


def test_guard_rail_rejects_empty_payload():
    with pytest.raises(GuardRailError) as exc:
        check_guard_rail(fetched_count=0, last_good_count=713)
    assert "zero events" in str(exc.value)


def test_guard_rail_rejects_a_first_sync_of_zero():
    with pytest.raises(GuardRailError):
        check_guard_rail(fetched_count=0, last_good_count=0)


def test_guard_rail_rejects_a_large_drop():
    with pytest.raises(GuardRailError) as exc:
        check_guard_rail(fetched_count=300, last_good_count=713)
    assert "300" in str(exc.value) and "713" in str(exc.value)


def test_guard_rail_allows_a_small_drop():
    check_guard_rail(fetched_count=700, last_good_count=713)


def test_guard_rail_boundary_is_exactly_seventy_percent():
    """Pin the boundary itself - every other test is far from it."""
    check_guard_rail(fetched_count=700, last_good_count=1000)  # exactly 70%
    with pytest.raises(GuardRailError):
        check_guard_rail(fetched_count=699, last_good_count=1000)
