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

## Docker

The deployment target is Docker on a Linux host (e.g. a TrueNAS server). Phase 1
has no long-running process — the CLI runs and exits — so the Compose service is
invoked on demand rather than kept up. Phase 2's Discord bot will become a
`restart: unless-stopped` service using the same image.

Build the image:

    docker compose build

Run a sync:

    docker compose run --rm paxbot sync

List a day:

    docker compose run --rm paxbot list --day 2026-09-04 --category Panels

The database lives at `/data/paxbot.db` inside the container (set via the
`PAXBOT_DB` environment variable, which the CLI reads whenever `--db` is not
given explicitly). `compose.yml` mounts `./data` on the host to `/data`. On
TrueNAS, point the left-hand side of that volume at a dataset, e.g.
`/mnt/tank/apps/paxbot/data:/data`, instead of a relative host path.

**Storage caveat:** that volume must be backed by a filesystem with proper
POSIX locking. A local dataset is fine; an NFS or SMB share is not, because
SQLite's WAL mode relies on locking guarantees those do not reliably provide,
and a bind mount over such a share can corrupt the database or hang.

The container publishes no ports — the bot only makes outbound connections to
the PAX schedule API (and, in phase 2, to Discord).

Scheduling a recurring sync is an external concern for now: add a cron entry
or a TrueNAS scheduled task that runs `docker compose run --rm paxbot sync`
on whatever cadence you want.

## Contributing

Read `ARCHITECTURE.md` first — the layer boundaries are deliberate. Decision records
are in `docs/adr/`.

## License

AGPL-3.0-or-later. See `LICENSE`.
