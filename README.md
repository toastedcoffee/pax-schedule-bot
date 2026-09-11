# paxbot

Browse, build and share PAX convention schedules from Discord.

The official schedule is hard to search, tedious to turn into a personal plan, and
impossible to share except by screenshot. paxbot puts the whole schedule in Discord,
remembers what you picked, and lets you post an event your friends can add with one
click.

Self-hosted. Your data stays on your machine.

## Status

Phase 1: the data layer and a CLI. Discord commands are not implemented yet.

## Requirements

- Python 3.11+

## Setup

    python -m pip install -e ".[dev]"

## Usage

An editable install does not reliably put a `paxbot` command on your PATH, so invoke
the CLI as a module:

    python -m paxbot sync                              # fetch the schedule
    python -m paxbot list --day 2026-09-04             # list a day
    python -m paxbot list --day 2026-09-04 --category Panels

## Adding another PAX

Add a block to `shows.toml`:

    [shows.east]
    name       = "PAX East 2027"
    base_url   = "https://east.paxsite.com"
    api_key    = "..."
    timezone   = "America/New_York"
    start_date = 2027-04-21
    end_date   = 2027-04-25

Recover the API key with `python scripts/find_api_key.py east`.

## Tests

    pytest              # fast, offline
    pytest -m live      # contract tests against the live API

## Contributing

Read `ARCHITECTURE.md` first — the layer boundaries are deliberate. Decision records
are in `docs/adr/`.

## License

AGPL-3.0-or-later. See `LICENSE`.
