"""Which shows the sync loop polls on a given tick."""
from datetime import UTC, date, datetime, timedelta

import pytest

from paxbot.config import Show
from paxbot.sync.policy import shows_to_sync

IN_SHOW, OFF_SEASON = 1, 4


def _show(slug, start, end, tz="America/Los_Angeles"):
    return Show(slug=slug, name=slug.upper(), base_url="https://example.invalid",
                api_key="k", timezone=tz, start_date=start, end_date=end)


WEST = _show("west", date(2026, 9, 4), date(2026, 9, 7))
EAST = _show("east", date(2026, 3, 26), date(2026, 3, 29))
AUS = _show("aus", date(2026, 10, 9), date(2026, 10, 11), "Australia/Melbourne")


def slugs(result):
    return [s.slug for s in result]


def test_a_never_synced_past_show_is_still_polled():
    """Without this, a show added to shows.toml after it ended is never
    polled, so never has data, so is permanently invisible."""
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    assert slugs(shows_to_sync([EAST], {}, now, IN_SHOW, OFF_SEASON)) == ["east"]


def test_a_finished_and_already_synced_show_is_not_polled():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    last = {"east": now - timedelta(days=30)}
    assert shows_to_sync([EAST], last, now, IN_SHOW, OFF_SEASON) == ()


def test_a_slug_missing_from_the_mapping_means_never_synced():
    """A newly added show is absent from last_ok, not mapped to None."""
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert slugs(shows_to_sync([AUS], {}, now, IN_SHOW, OFF_SEASON)) == ["aus"]


def test_an_explicit_none_means_the_same_as_missing():
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    result = shows_to_sync([AUS], {"aus": None}, now, IN_SHOW, OFF_SEASON)
    assert slugs(result) == ["aus"]


def test_a_running_show_is_due_after_one_hour():
    now = datetime(2026, 9, 5, 18, tzinfo=UTC)
    assert slugs(shows_to_sync([WEST], {"west": now - timedelta(hours=1)},
                               now, IN_SHOW, OFF_SEASON)) == ["west"]


def test_a_running_show_is_not_due_after_fifty_minutes():
    now = datetime(2026, 9, 5, 18, tzinfo=UTC)
    assert shows_to_sync([WEST], {"west": now - timedelta(minutes=50)},
                         now, IN_SHOW, OFF_SEASON) == ()


def test_an_off_season_show_waits_four_hours_not_one():
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    last = {"west": now - timedelta(hours=2)}
    assert shows_to_sync([WEST], last, now, IN_SHOW, OFF_SEASON) == ()
    last = {"west": now - timedelta(hours=4)}
    assert slugs(shows_to_sync([WEST], last, now, IN_SHOW, OFF_SEASON)) == ["west"]


def test_a_due_show_and_a_not_due_show_in_the_same_tick():
    """Both shows are eligible, so recency alone decides: west is running and
    was synced 10 minutes ago (inside its hour), aus is upcoming and was
    synced 5 hours ago (past its four)."""
    now = datetime(2026, 9, 5, 18, tzinfo=UTC)
    last = {"aus": now - timedelta(hours=5), "west": now - timedelta(minutes=10)}
    assert slugs(shows_to_sync([WEST, AUS], last, now, IN_SHOW, OFF_SEASON)) == ["aus"]


def test_eligibility_uses_the_shows_own_timezone():
    """2026-09-08 06:00 UTC is still Sep 7 in Seattle, so West has NOT ended
    and is still eligible - judging in UTC would stop polling a day early."""
    now = datetime(2026, 9, 8, 6, tzinfo=UTC)
    last = {"west": now - timedelta(hours=6)}
    assert slugs(shows_to_sync([WEST], last, now, IN_SHOW, OFF_SEASON)) == ["west"]


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        shows_to_sync([WEST], {}, datetime(2026, 9, 5, 12), IN_SHOW, OFF_SEASON)


def test_order_follows_the_input():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    assert slugs(shows_to_sync([EAST, AUS], {}, now, IN_SHOW, OFF_SEASON)) == ["east", "aus"]
