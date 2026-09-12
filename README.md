# pax-schedule-bot

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

Invoke the CLI as a module (this also works without installing the package):

    python -m paxbot sync                              # fetch the schedule
    python -m paxbot list --day 2026-09-04             # list a day
    python -m paxbot list --day 2026-09-04 --category Panels

`--category` must match the upstream category string exactly, including case
("Panels" will not match "panels"). To see the real category names for a
show, query the database directly after a sync. `python:3.13-slim` (the
deployment container's base image) has no `sqlite3` CLI, and a TrueNAS
operator may have no host Python either, so run the query through Python
inside the container instead:

    docker compose run --rm --entrypoint python paxbot -c \
      "import sqlite3;print(*sorted({r[0] for r in sqlite3.connect('/data/paxbot.db').execute('SELECT DISTINCT category FROM event_categories')}),sep='\n')"

Outside the container, with a local Python and the database at `paxbot.db`,
the equivalent is:

    python -c "import sqlite3;print(*sorted({r[0] for r in sqlite3.connect('paxbot.db').execute('SELECT DISTINCT category FROM event_categories')}),sep='\n')"

Config resolution follows the same override order as the database path:
`--config` wins, then the `PAXBOT_CONFIG` environment variable, then
`./shows.toml` in the current directory, then the copy shipped beside the
installed package.

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

### Annual rollover

Do not repoint an existing slug (e.g. `[shows.west]`) at next year's show. The
new year's schedule starts small and grows, and a repointed slug would make
this year's ~713 events look cancelled the moment the new one syncs in under
the same name - permanently, since events are never deleted, only marked
cancelled.

Instead, give each year its own slug:

    [shows.west-2026]
    name       = "PAX West 2026"
    ...

    [shows.west-2027]
    name       = "PAX West 2027"
    base_url   = "https://west.paxsite.com"
    api_key    = "..."
    timezone   = "America/Los_Angeles"
    start_date = 2027-09-03
    end_date   = 2027-09-06

`paxbot sync --show west-2027` and `paxbot list --show west-2027 ...` then
address the new year, while `west-2026`'s events stay in the store and
addressable under their own slug. See
`docs/adr/0005-one-show-slug-per-year.md`.

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

The container runs as a fixed non-root user, uid/gid 10001. Before the first
run, create the host data directory and hand it to that uid - Docker does
not do this for you, and a directory Compose auto-creates for a bind mount
comes back owned by root, which uid 10001 cannot write to:

    mkdir -p ./data
    sudo chown 10001:10001 ./data

Run a sync:

    docker compose run --rm paxbot sync

List a day:

    docker compose run --rm paxbot list --day 2026-09-04 --category Panels

The database lives at `/data/paxbot.db` inside the container (set via the
`PAXBOT_DB` environment variable, which the CLI reads whenever `--db` is not
given explicitly). `compose.yml` mounts `./data` on the host to `/data`. On
TrueNAS, point the left-hand side of that volume at a dataset, e.g.
`/mnt/tank/apps/paxbot/data:/data`, instead of a relative host path.

`compose.yml` also mounts `./shows.toml` read-only to `/app/shows.toml` and
sets `PAXBOT_CONFIG=/app/shows.toml` (see "Usage" above for the full
resolution order). Adding a convention, or editing dates or an API key, is
therefore a host-side edit to `shows.toml` - no image rebuild required.

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
