import json
from datetime import date
from pathlib import Path

import pytest

from paxbot.config import Show
from paxbot.store.db import connect
from paxbot.store.events import event_count, get_event
from paxbot.sync.diff import GuardRailError
from paxbot.sync.runner import run_sync

FIXTURES = Path(__file__).parent / "fixtures"

WEST = Show(
    slug="west", name="PAX West 2026", base_url="https://west.paxsite.com",
    api_key="k", timezone="America/Los_Angeles",
    start_date=date(2026, 9, 4), end_date=date(2026, 9, 7),
)


def fetcher_for(name: str):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return lambda show: payload


def fetcher_returning(payload: dict):
    return lambda show: payload


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def test_first_sync_inserts_everything(conn):
    report = run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    assert report.ok is True
    assert report.added == 5
    assert event_count(conn, "west") == 5


def test_second_identical_sync_changes_nothing(conn):
    fetch = fetcher_for("schedules_sample.json")
    run_sync(conn, WEST, fetcher=fetch)
    report = run_sync(conn, WEST, fetcher=fetch)
    assert (report.added, report.changed, report.removed) == (0, 0, 0)
    assert report.unchanged == 5


def test_changed_event_is_detected_and_written(conn):
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    payload = json.loads((FIXTURES / "schedules_sample.json").read_text(encoding="utf-8"))
    payload["schedules"][0]["location"] = "Main Theatre (LVL 5)"
    report = run_sync(conn, WEST, fetcher=fetcher_returning(payload))
    assert report.changed == 1
    assert get_event(conn, "west", "943694").location == "Main Theatre (LVL 5)"


def test_vanished_event_is_marked_cancelled_not_deleted(conn):
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    payload = json.loads((FIXTURES / "schedules_sample.json").read_text(encoding="utf-8"))
    payload["schedules"] = payload["schedules"][:-1]
    report = run_sync(conn, WEST, fetcher=fetcher_returning(payload))
    assert report.removed == 1
    assert event_count(conn, "west") == 4
    assert get_event(conn, "west", "943681") is not None


def test_guard_rail_rejects_and_preserves_previous_data(conn):
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    empty = {"event_name": "PAX West 2026", "schedules": []}
    with pytest.raises(GuardRailError):
        run_sync(conn, WEST, fetcher=fetcher_returning(empty))
    assert event_count(conn, "west") == 5


def test_skipped_records_are_reported_not_fatal(conn):
    report = run_sync(conn, WEST, fetcher=fetcher_for("edge_malformed.json"))
    assert report.ok is True
    assert report.added == 1
    assert report.skipped == 3
