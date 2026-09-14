"""The bot's startup checks. Owner report, 2026-09-14: on TrueNAS the bot
stopped with only "unable to open database file". Docker had created the
missing data folder as root, and nothing said so."""
import os

import pytest

from paxbot import app


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
