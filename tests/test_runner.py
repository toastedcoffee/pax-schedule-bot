import json
from datetime import date
from pathlib import Path

import pytest

from paxbot.config import Show
from paxbot.sources.leap import LeapError
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


def test_guard_rail_rejection_is_recorded(conn):
    """The audit table must record attempts, not just successes."""
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    with pytest.raises(GuardRailError):
        run_sync(conn, WEST, fetcher=fetcher_returning({"schedules": []}))
    rows = conn.execute("SELECT ok, note FROM sync_runs ORDER BY rowid").fetchall()
    assert [r["ok"] for r in rows] == [1, 0]
    assert "zero events" in rows[-1]["note"]


def test_a_write_failure_rolls_back_and_is_recorded(conn, monkeypatch):
    import paxbot.sync.runner as runner_module

    def boom(_conn, _events):
        raise RuntimeError("disk died mid-write")

    monkeypatch.setattr(runner_module, "upsert_events", boom)
    with pytest.raises(RuntimeError):
        run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))

    assert event_count(conn, "west") == 0  # rolled back
    rows = conn.execute("SELECT ok, note FROM sync_runs").fetchall()
    assert [r["ok"] for r in rows] == [0]
    assert "write failed" in rows[0]["note"]


def test_a_broken_audit_write_does_not_mask_the_real_error(conn, monkeypatch):
    """A dead disk breaks the write AND the audit row. The caller needs the
    root cause, not a secondary error from the bookkeeping."""
    import paxbot.sync.runner as runner_module

    def boom_write(_conn, _events):
        raise RuntimeError("disk died mid-write")

    def boom_audit(*_args, **_kwargs):
        raise OSError("disk full writing audit row")

    monkeypatch.setattr(runner_module, "upsert_events", boom_write)
    monkeypatch.setattr(runner_module, "_record_run", boom_audit)
    with pytest.raises(RuntimeError, match="disk died mid-write"):
        run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))


def test_a_fetch_failure_is_recorded(conn):
    def boom(_show):
        raise LeapError("api down")

    with pytest.raises(LeapError):
        run_sync(conn, WEST, fetcher=boom)
    rows = conn.execute("SELECT ok, note FROM sync_runs").fetchall()
    assert [r["ok"] for r in rows] == [0]
    assert "api down" in rows[0]["note"]


def test_skipped_records_are_reported_not_fatal(conn):
    report = run_sync(conn, WEST, fetcher=fetcher_for("edge_malformed.json"))
    assert report.added == 1
    assert report.skipped == 3


def test_a_description_only_change_is_detected(conn):
    import copy

    payload = json.loads((FIXTURES / "schedules_sample.json").read_text(encoding="utf-8"))
    run_sync(conn, WEST, fetcher=fetcher_returning(payload))
    edited = copy.deepcopy(payload)
    edited["schedules"][0]["description"] = "Rewritten upstream."
    report = run_sync(conn, WEST, fetcher=fetcher_returning(edited))
    assert report.changed == 1
    assert get_event(conn, "west", "943694").description == "Rewritten upstream."


def test_a_parsed_events_category_order_survives_the_round_trip(conn):
    """SQLite's BINARY collation would otherwise sort TTRPGs before Tabletop.

    Reproduced on the committed fixture: event 945852 parses as
    ('Age 13+', 'Tabletop', 'TTRPGs') and previously read back as
    ('Age 13+', 'TTRPGs', 'Tabletop').
    """
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    assert get_event(conn, "west", "945852").categories == (
        "Age 13+", "Tabletop", "TTRPGs",
    )


def test_sync_runs_ran_at_is_utc(conn):
    """The global constraint is UTC; a text ORDER BY ran_at must sort cleanly."""
    run_sync(conn, WEST, fetcher=fetcher_for("schedules_sample.json"))
    ran_at = conn.execute("SELECT ran_at FROM sync_runs").fetchone()["ran_at"]
    assert ran_at.endswith("+00:00")
