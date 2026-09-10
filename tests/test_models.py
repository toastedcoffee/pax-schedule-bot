from datetime import UTC, date, datetime

from paxbot.models import Event, compute_row_hash


def make_event(**overrides) -> Event:
    base = dict(
        show_slug="west",
        gt_id="943694",
        title="Can't Stop",
        description="Max of 16 players.",
        starts_at=datetime(2026, 9, 4, 18, 30, tzinfo=UTC),
        ends_at=datetime(2026, 9, 4, 22, 0, tzinfo=UTC),
        day=date(2026, 9, 4),
        location="Tabletop Tourney (LVL 2)",
        url="https://west.paxsite.com/x",
        categories=("Tabletop", "Tournaments"),
        row_hash="abc",
    )
    base.update(overrides)
    return Event(**base)


def test_duration_minutes():
    assert make_event().duration_minutes == 210


def test_short_event_is_not_a_drop_in():
    assert make_event().is_drop_in(240) is False


def test_long_event_is_a_drop_in():
    long = make_event(ends_at=datetime(2026, 9, 5, 6, 30, tzinfo=UTC))  # 12h
    assert long.is_drop_in(240) is True


def test_drop_in_boundary_is_inclusive():
    exactly_four = make_event(ends_at=datetime(2026, 9, 4, 22, 30, tzinfo=UTC))
    assert exactly_four.duration_minutes == 240
    assert exactly_four.is_drop_in(240) is True


def test_row_hash_is_stable_and_order_independent():
    a = compute_row_hash("T", "2026-09-04T18:30:00+00:00", "2026-09-04T22:00:00+00:00",
                         "Room", ("B", "A"))
    b = compute_row_hash("T", "2026-09-04T18:30:00+00:00", "2026-09-04T22:00:00+00:00",
                         "Room", ("A", "B"))
    assert a == b


def test_row_hash_changes_when_location_changes():
    a = compute_row_hash("T", "s", "e", "Room 1", ("A",))
    b = compute_row_hash("T", "s", "e", "Room 2", ("A",))
    assert a != b


def test_row_hash_is_unambiguous_when_a_field_contains_a_pipe():
    """32 of 713 real PAX titles contain "|". These two collide under a
    "|"-joined payload: both flatten to
    'Magic: The Gathering|The Hobbit|2026-09-04T18:30:00+00:00|e|Room|X'."""
    a = compute_row_hash(
        "Magic: The Gathering|The Hobbit", "2026-09-04T18:30:00+00:00", "e", "Room", ("X",)
    )
    b = compute_row_hash(
        "Magic: The Gathering", "The Hobbit|2026-09-04T18:30:00+00:00", "e", "Room", ("X",)
    )
    assert a != b


def test_row_hash_distinguishes_one_comma_category_from_two_categories():
    """A ","-joined category list cannot tell these apart."""
    a = compute_row_hash("T", "s", "e", "Room", ("Tabletop,Tournaments",))
    b = compute_row_hash("T", "s", "e", "Room", ("Tabletop", "Tournaments"))
    assert a != b
