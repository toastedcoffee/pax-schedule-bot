"""Shared test factories. tests/test_store.py keeps its own local helper."""
from datetime import UTC, date, datetime, timedelta

import pytest

from paxbot.models import Event
from paxbot.store.db import connect


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def make_event(
    gt_id="1",
    title="Panel",
    hour=18,
    minute=0,
    minutes=60,
    categories=("Panels",),
    day=None,
    location="Room A",
    show_slug="west",
):
    """Build an Event. `hour`/`minute` are UTC; `minutes` is the duration.

    Duration is explicit rather than derived from an end hour so that drop-in
    cases (>= 240 minutes) and midnight-crossing events are both expressible.
    """
    day = day or date(2026, 9, 4)
    starts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return Event(
        show_slug=show_slug,
        gt_id=gt_id,
        title=title,
        description="d",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=minutes),
        day=day,
        location=location,
        url="https://example.invalid/e",
        categories=categories,
        row_hash=f"hash-{gt_id}-{title}",
    )
