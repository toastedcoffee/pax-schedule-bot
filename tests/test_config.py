from datetime import date, datetime
from pathlib import Path
from textwrap import dedent
from zoneinfo import ZoneInfo

import pytest

from paxbot.config import DayError, Show, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_loads_west_show():
    cfg = load_config(REPO_ROOT / "shows.toml")
    west = cfg.show("west")
    assert west.slug == "west"
    assert west.name == "PAX West 2026"
    assert west.base_url == "https://west.paxsite.com"
    assert west.timezone == "America/Los_Angeles"
    assert west.start_date == date(2026, 9, 4)
    assert west.end_date == date(2026, 9, 7)
    assert west.api_key == "b0c56126-dbbd-4cee-a027-42f7b2c2a2d0"


def test_base_url_trailing_slash_is_stripped(tmp_path):
    toml_path = tmp_path / "shows.toml"
    toml_path.write_text(
        dedent(
            """\
            [shows.trailing]
            name       = "Trailing Slash Show"
            base_url   = "https://trailing.paxsite.com/"
            api_key    = "test-api-key"
            timezone   = "America/Los_Angeles"
            start_date = 2026-09-04
            end_date   = 2026-09-07
            """
        ),
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    trailing = cfg.show("trailing")
    assert trailing.base_url == "https://trailing.paxsite.com"


def test_drop_in_threshold_defaults_to_240():
    cfg = load_config(REPO_ROOT / "shows.toml")
    assert cfg.drop_in_threshold_minutes == 240


def test_unknown_show_lists_available_slugs():
    cfg = load_config(REPO_ROOT / "shows.toml")
    with pytest.raises(KeyError) as exc:
        cfg.show("nope")
    assert "west" in str(exc.value)


WEST = Show(
    slug="west",
    name="PAX West 2026",
    base_url="https://west.paxsite.com",
    api_key="k",
    timezone="America/Los_Angeles",
    start_date=date(2026, 9, 4),
    end_date=date(2026, 9, 7),
)
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC_TZ = ZoneInfo("UTC")


def test_is_running_inside_the_show():
    assert WEST.is_running(datetime(2026, 9, 5, 14, 0, tzinfo=PACIFIC)) is True


def test_is_running_on_first_and_last_day():
    assert WEST.is_running(datetime(2026, 9, 4, 0, 1, tzinfo=PACIFIC)) is True
    assert WEST.is_running(datetime(2026, 9, 7, 23, 59, tzinfo=PACIFIC)) is True


def test_is_running_outside_the_show():
    assert WEST.is_running(datetime(2026, 9, 3, 23, 59, tzinfo=PACIFIC)) is False
    assert WEST.is_running(datetime(2026, 9, 8, 0, 1, tzinfo=PACIFIC)) is False


def test_is_running_converts_to_show_timezone():
    """2026-09-04 06:00 UTC is 2026-09-03 23:00 Pacific - still before the show.

    Pacific is UTC-7 in September. Comparing the UTC date directly would read
    this as the 4th and wrongly report the show as running.
    """
    assert WEST.is_running(datetime(2026, 9, 4, 6, 0, tzinfo=UTC_TZ)) is False
    assert WEST.is_running(datetime(2026, 9, 4, 8, 0, tzinfo=UTC_TZ)) is True


def test_is_running_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        WEST.is_running(datetime(2026, 9, 5, 14, 0))


def _show(start, end):
    return Show(slug="x", name="PAX Test", base_url="https://x.invalid",
                api_key="k", timezone="America/Los_Angeles",
                start_date=start, end_date=end)


def test_days_lists_every_show_date_in_order():
    assert WEST.days() == (date(2026, 9, 4), date(2026, 9, 5),
                           date(2026, 9, 6), date(2026, 9, 7))


@pytest.mark.parametrize("typed", [
    "Saturday", "saturday", "SATURDAY", "Sat", "sat", "satur", "  sat  ",
    "2026-09-05",
])
def test_resolve_day_accepts_names_abbreviations_and_dates(typed):
    assert WEST.resolve_day(typed) == date(2026, 9, 5)


def test_resolve_day_accepts_the_longer_abbreviations_people_write():
    """Tues and Thurs are how those days are commonly shortened."""
    show = _show(date(2026, 9, 1), date(2026, 9, 3))   # Tuesday to Thursday
    assert show.resolve_day("tues") == date(2026, 9, 1)
    assert show.resolve_day("thurs") == date(2026, 9, 3)


def test_resolve_day_rejects_a_weekday_the_show_does_not_include():
    with pytest.raises(DayError, match="Friday, Sep 04 to Monday, Sep 07"):
        WEST.resolve_day("Tuesday")


def test_resolve_day_rejects_a_date_outside_the_show():
    with pytest.raises(DayError, match="not a day of"):
        WEST.resolve_day("2026-12-25")


def test_resolve_day_needs_at_least_three_letters():
    """One or two letters cannot tell Saturday from Sunday."""
    with pytest.raises(DayError, match="three letters"):
        WEST.resolve_day("s")


def test_resolve_day_rejects_a_weekday_that_names_two_dates():
    """A show longer than a week has two Saturdays; guessing one would be
    silently wrong half the time."""
    long_show = _show(date(2026, 9, 5), date(2026, 9, 13))
    with pytest.raises(DayError, match="more than one"):
        long_show.resolve_day("Saturday")
    assert long_show.resolve_day("2026-09-12") == date(2026, 9, 12)


def test_resolve_day_error_clips_what_it_echoes_back():
    """The message goes straight to Discord, whose content cap is 2000."""
    with pytest.raises(DayError) as caught:
        WEST.resolve_day("x" * 5000)
    assert len(str(caught.value)) < 300


def test_day_error_is_a_value_error():
    """Callers that already catch ValueError keep working."""
    assert issubclass(DayError, ValueError)

