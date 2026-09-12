from datetime import date, datetime
from pathlib import Path
from textwrap import dedent
from zoneinfo import ZoneInfo

import pytest

from paxbot.config import Show, load_config

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
