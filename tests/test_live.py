"""Contract tests against the real API. Deselected by default.

Fixture tests catch our bugs; this catches upstream's changes.
Run with: pytest -m live
"""
from datetime import datetime
from pathlib import Path

import pytest

from paxbot.config import load_config
from paxbot.sources.leap import fetch_schedules
from paxbot.sources.parse import API_TIME_FORMAT, parse_schedules

CONFIG_PATH = Path(__file__).resolve().parents[1] / "shows.toml"

# Module-level guard: every test here is live, marker or not. Without this a
# test added later without @pytest.mark.live would silently hit the real API on
# every default `pytest` run - and the module-scoped payload fixture below
# performs a real fetch.
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def show():
    return load_config(CONFIG_PATH).show("west")


@pytest.fixture(scope="module")
def payload(show):
    return fetch_schedules(show)


@pytest.mark.live
def test_api_returns_a_populated_schedule(payload):
    assert isinstance(payload["schedules"], list)
    assert len(payload["schedules"]) > 100


@pytest.mark.live
def test_every_record_has_the_fields_we_depend_on(payload):
    for record in payload["schedules"]:
        assert record.get("id"), "record without id"
        assert record.get("title") is not None
        datetime.strptime(record["start_time"], API_TIME_FORMAT)
        datetime.strptime(record["end_time"], API_TIME_FORMAT)


@pytest.mark.live
def test_the_real_payload_parses_without_mass_skipping(payload, show):
    result = parse_schedules(payload, show)
    assert len(result.events) > 100
    assert len(result.skipped) < len(result.events) * 0.05
