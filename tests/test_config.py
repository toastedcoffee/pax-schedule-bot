from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from paxbot.config import load_config

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
