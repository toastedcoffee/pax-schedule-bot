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


def test_no_db_file_is_created_when_day_is_invalid(tmp_path):
    """connect() must run after argument validation, not before."""
    db_path = tmp_path / "should-not-exist.db"
    assert main(["list", "--day", "nope", "--db", str(db_path)]) == 2
    assert not db_path.exists()


def test_missing_config_file_is_reported_cleanly(tmp_path, capsys):
    """--config wins over everything, including a valid shows.toml in cwd."""
    missing = tmp_path / "does-not-exist.toml"
    assert main(["sync", "--config", str(missing)]) == 2
    err = capsys.readouterr().err
    assert err.strip() != ""
    assert "Traceback" not in err


def test_malformed_config_file_is_reported_cleanly(tmp_path, capsys):
    bad_config = tmp_path / "shows.toml"
    bad_config.write_text("this is [ not valid toml", encoding="utf-8")
    assert main(["sync", "--config", str(bad_config)]) == 2
    err = capsys.readouterr().err
    assert err.strip() != ""
    assert "Traceback" not in err


def _write_config(path: Path, slug: str) -> None:
    path.write_text(
        f"""
[defaults]
drop_in_threshold_minutes = 240

[shows.{slug}]
name       = "Alt Show"
base_url   = "https://example.invalid"
api_key    = "test-key"
timezone   = "UTC"
start_date = 2030-01-01
end_date   = 2030-01-02
""",
        encoding="utf-8",
    )


def test_config_flag_is_honoured(db, tmp_path, capsys):
    alt = tmp_path / "alt-shows.toml"
    _write_config(alt, "altshow")
    assert main(["sync", "--config", str(alt), "--show", "altshow", "--db", db]) == 0
    assert "altshow: added" in capsys.readouterr().out


def test_paxbot_config_env_var_is_honoured_when_the_flag_is_absent(
    db, tmp_path, monkeypatch, capsys
):
    """A container sets PAXBOT_CONFIG once via compose instead of every command
    repeating --config, mirroring how PAXBOT_DB already works."""
    alt = tmp_path / "alt-shows.toml"
    _write_config(alt, "altshow")
    monkeypatch.setenv("PAXBOT_CONFIG", str(alt))
    assert main(["sync", "--show", "altshow", "--db", db]) == 0
    assert "altshow: added" in capsys.readouterr().out


def test_config_flag_beats_the_env_var(db, tmp_path, monkeypatch, capsys):
    env_cfg = tmp_path / "env-shows.toml"
    flag_cfg = tmp_path / "flag-shows.toml"
    _write_config(env_cfg, "envshow")
    _write_config(flag_cfg, "flagshow")
    monkeypatch.setenv("PAXBOT_CONFIG", str(env_cfg))
    assert main(["sync", "--config", str(flag_cfg), "--show", "flagshow", "--db", db]) == 0
    assert "flagshow: added" in capsys.readouterr().out


def test_shows_toml_in_the_current_directory_beats_the_packaged_copy(
    db, tmp_path, monkeypatch, capsys
):
    """A container's WORKDIR shadowing an installed copy is the whole point of
    the cwd lookup; this is the same behaviour without a container."""
    local_dir = tmp_path / "cwd"
    local_dir.mkdir()
    _write_config(local_dir / "shows.toml", "cwdshow")
    monkeypatch.chdir(local_dir)
    assert main(["sync", "--show", "cwdshow", "--db", db]) == 0
    assert "cwdshow: added" in capsys.readouterr().out


def test_packaged_config_is_the_last_resort_fallback(db, tmp_path, monkeypatch, capsys):
    """No --config, no PAXBOT_CONFIG, and no ./shows.toml: falls back to the
    copy shipped beside the package (the repo's own shows.toml in this dev
    checkout)."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    assert main(["sync", "--db", db]) == 0
    assert "west: added" in capsys.readouterr().out
