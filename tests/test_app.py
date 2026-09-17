"""The bot's startup checks. Owner report, 2026-09-14: on TrueNAS the bot
stopped with only "unable to open database file". Docker had created the
missing data folder as root, and nothing said so."""
import os
from datetime import UTC, date, datetime

import pytest

from paxbot import app
from paxbot.config import Show
from paxbot.store.db import connect


def test_a_missing_data_folder_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("PAXBOT_DB", str(tmp_path / "missing" / "paxbot.db"))
    with pytest.raises(app.StartupError) as caught:
        app.build()
    message = str(caught.value)
    assert "unable to open database file" in message
    assert "does not exist" in message


def test_an_unwritable_data_folder_says_who_needs_write_access(tmp_path, monkeypatch):
    real_access = os.access
    monkeypatch.setattr(app.os, "access", lambda path, mode: (
        False if mode == os.W_OK else real_access(path, mode)))
    hint = app._database_hint(str(tmp_path / "paxbot.db"))
    assert "not writable" in hint
    assert "root" in hint
    assert "568" in hint


def test_a_writable_data_folder_adds_no_hint(tmp_path):
    assert app._database_hint(str(tmp_path / "paxbot.db")) == ""


def test_an_unwritable_database_file_in_a_writable_folder_is_named(tmp_path, monkeypatch):
    """chown without -R fixes the folder but leaves an old owner's files."""
    db = tmp_path / "paxbot.db"
    db.touch()
    real_access = os.access
    monkeypatch.setattr(app.os, "access", lambda path, mode: (
        False if mode == os.W_OK and os.fspath(path) == str(db)
        else real_access(path, mode)))
    hint = app._database_hint(str(db))
    assert str(db) in hint
    assert "not writable" in hint
    assert "-R" in hint


def test_last_ok_syncs_maps_each_show_to_its_latest_good_run(tmp_path):
    conn = connect(str(tmp_path / "db.sqlite"))
    try:
        early = datetime(2026, 9, 1, 8, tzinfo=UTC)
        late = datetime(2026, 9, 2, 8, tzinfo=UTC)
        for slug, ran_at, ok in [("west", early, 1), ("west", late, 1),
                                 ("east", early, 1),
                                 ("aus", late, 0)]:
            conn.execute(
                "INSERT INTO sync_runs (show_slug, ran_at, event_count, ok, note) "
                "VALUES (?, ?, ?, ?, '')", (slug, ran_at.isoformat(), 1, ok))
        result = app._last_ok_syncs(conn)
        assert result["west"] == late
        assert result["east"] == early
        # A failed run is not a successful one, so aus must read as never synced.
        assert "aus" not in result
    finally:
        conn.close()


def test_a_failing_show_does_not_stop_the_others(tmp_path, monkeypatch):
    """One broken show must never cost the others their sync."""

    def show(slug):
        return Show(slug=slug, name=slug, base_url="https://e.invalid", api_key="k",
                    timezone="America/Los_Angeles",
                    start_date=date(2026, 9, 4), end_date=date(2026, 9, 7))

    synced = []

    def fake_run_sync(conn, s):
        if s.slug == "broken":
            raise RuntimeError("upstream is down")
        synced.append(s.slug)
        return app.SyncReport(show_slug=s.slug)

    monkeypatch.setattr(app, "run_sync", fake_run_sync)
    app._sync_due(str(tmp_path / "db.sqlite"), [show("broken"), show("west")])
    assert synced == ["west"]
