import json
from pathlib import Path

import pytest

from paxbot.cli import _ascii, main

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def offline_fetch(monkeypatch):
    payload = json.loads((FIXTURES / "schedules_sample.json").read_text(encoding="utf-8"))
    monkeypatch.setattr("paxbot.cli.fetch_schedules", lambda show: payload)


def test_sync_reports_counts(db, capsys):
    assert main(["sync", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "added 5" in out


def test_list_shows_events_in_time_order(db, capsys):
    main(["sync", "--db", db])
    capsys.readouterr()
    assert main(["list", "--day", "2026-09-04", "--db", db]) == 0
    out = capsys.readouterr().out
    assert out.index("Crokinole") < out.index("Can't Stop")
    assert "Tabletop Tourney (LVL 2)" in out


def test_list_marks_drop_ins(db, capsys):
    main(["sync", "--db", db])
    capsys.readouterr()
    main(["list", "--day", "2026-09-04", "--db", db])
    out = capsys.readouterr().out
    assert "[drop-in]" in out


def test_list_filters_by_category(db, capsys):
    main(["sync", "--db", db])
    capsys.readouterr()
    main(["list", "--day", "2026-09-04", "--category", "Tournaments", "--db", db])
    out = capsys.readouterr().out
    assert "Can't Stop" in out
    assert "Crokinole" not in out


def test_times_render_in_the_shows_timezone_not_utc(db, capsys):
    """11:30 PDT is 18:30 UTC. Printing the raw instant would say 6:30pm."""
    main(["sync", "--db", db])
    capsys.readouterr()
    main(["list", "--day", "2026-09-04", "--db", db])
    out = capsys.readouterr().out
    assert "11:30am" in out
    assert "6:30pm" not in out


def test_upstream_text_is_folded_to_ascii():
    """The fixture is clean ASCII; real PAX data is not."""
    assert _ascii("Samantha Béart") == "Samantha Beart"
    assert _ascii("Game — The Next Chapter") == "Game - The Next Chapter"
    assert _ascii("Village in the Shade’s") == "Village in the Shade's"


def test_output_is_ascii_only(db, capsys):
    """Windows consoles default to cp1252; non-ASCII renders as garbage."""
    main(["sync", "--db", db])
    capsys.readouterr()
    main(["list", "--day", "2026-09-04", "--db", db])
    out = capsys.readouterr().out
    assert out.isascii(), [c for c in set(out) if not c.isascii()]


def test_unknown_show_exits_nonzero(db, capsys):
    assert main(["sync", "--show", "nope", "--db", db]) == 2
    assert "unknown show" in capsys.readouterr().err


def test_db_path_comes_from_the_env_var_when_the_flag_is_absent(tmp_path, monkeypatch):
    """Compose sets the path once; commands should not repeat --db."""
    envdb = tmp_path / "from-env.db"
    monkeypatch.setenv("PAXBOT_DB", str(envdb))
    assert main(["sync"]) == 0
    assert envdb.exists()


def test_an_explicit_db_flag_beats_the_env_var(tmp_path, monkeypatch):
    envdb = tmp_path / "from-env.db"
    flagdb = tmp_path / "from-flag.db"
    monkeypatch.setenv("PAXBOT_DB", str(envdb))
    assert main(["sync", "--db", str(flagdb)]) == 0
    assert flagdb.exists()
    assert not envdb.exists()
