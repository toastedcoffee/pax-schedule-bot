# pax-schedule-bot

Browse, build and share PAX convention schedules from Discord.

The official schedule is hard to search, tedious to turn into a personal plan, and
impossible to share except by screenshot. paxbot puts the whole schedule in Discord,
remembers what you picked, and lets you post an event your friends can add with one
click.

Self-hosted. Your data stays on your machine.

## Status

Phase 1: the data layer and a CLI. Discord commands are not implemented yet.

## Two ways to run it

Docker is the deployment target — a container on a Linux host (e.g. a TrueNAS
server) is the setup this project is built and tested against. A plain Python
install also works and is useful for development, or if you'd rather not run
Docker at all. Pick one.

### Option A: Docker (recommended)

Phase 1 has no long-running process — the CLI runs and exits — so the Compose
service is invoked on demand rather than kept up. Phase 2's Discord bot will
become a `restart: unless-stopped` service using the same image.

**Build the image:**

    docker compose build

**Create the host data directory and hand it to the container's user.** The
container runs as a fixed non-root uid/gid, 10001. Docker does not do this for
you — a directory Compose auto-creates for a bind mount comes back owned by
root, which uid 10001 cannot write to:

    mkdir -p ./data
    sudo chown 10001:10001 ./data

**Run a sync:**

    docker compose run --rm paxbot sync

**List a day:**

    docker compose run --rm paxbot list --day 2026-09-04 --category Panels

The database lives at `/data/paxbot.db` inside the container (set via the
`PAXBOT_DB` environment variable, which the CLI reads whenever `--db` is not
given explicitly). `compose.yml` mounts `./data` on the host to `/data`. On
TrueNAS, point the left-hand side of that volume at a dataset, e.g.
`/mnt/tank/apps/paxbot/data:/data`, instead of a relative host path.

`compose.yml` also mounts `./shows.toml` read-only to `/app/shows.toml` and
sets `PAXBOT_CONFIG=/app/shows.toml`. Adding a convention, or editing dates or
an API key, is therefore a host-side edit to `shows.toml` — **no image rebuild
required.**

To find real category names for `--category`, run the query through Python
inside the container (`python:3.13-slim`, the base image, has no `sqlite3`
CLI, and a TrueNAS operator may have no host Python either):

    docker compose run --rm --entrypoint python paxbot -c \
      "import sqlite3;print(*sorted({r[0] for r in sqlite3.connect('/data/paxbot.db').execute('SELECT DISTINCT category FROM event_categories')}),sep='\n')"

**Storage caveat:** the `/data` volume must be backed by a filesystem with
proper POSIX locking. A local dataset is fine; an NFS or SMB share is not,
because SQLite's WAL mode relies on locking guarantees those do not reliably
provide, and a bind mount over such a share can corrupt the database or hang.

The container publishes no ports — the bot only makes outbound connections to
the PAX schedule API (and, in phase 2, to Discord).

Scheduling a recurring sync is an external concern for now: add a cron entry
or a TrueNAS scheduled task that runs `docker compose run --rm paxbot sync`
on whatever cadence you want.

### Option B: Standalone Python

No Docker. Requires Python 3.11+.

**Install:**

    python -m pip install -e ".[dev]"

**Run it as a module** — this is the invocation to use. A `paxbot` console
script is declared, but an editable install does not reliably put it on your
PATH; `python -m paxbot` always works regardless:

    python -m paxbot sync                              # fetch the schedule
    python -m paxbot list --day 2026-09-04              # list a day
    python -m paxbot list --day 2026-09-04 --category Panels

By default the database is `paxbot.db` in the current directory and the show
config is the `shows.toml` shipped with the repo. To find real category names
for `--category`:

    python -c "import sqlite3;print(*sorted({r[0] for r in sqlite3.connect('paxbot.db').execute('SELECT DISTINCT category FROM event_categories')}),sep='\n')"

### Config and database resolution (both options)

Both the database path and the show config follow the same override order,
whether you're in a container or running standalone:

| | Flag | Environment variable | Default |
|---|---|---|---|
| Database | `--db` | `PAXBOT_DB` | `paxbot.db` in the current directory |
| Config | `--config` | `PAXBOT_CONFIG` | `./shows.toml`, else the copy shipped with the package |

`--category` must match the upstream category string exactly, including case
("Panels" will not match "panels").

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

Under Docker, editing `shows.toml` on the host is enough — the file is
mounted, not baked into the image, so no rebuild is needed.

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

## Contributing

Read `ARCHITECTURE.md` first — the layer boundaries are deliberate. Decision records
are in `docs/adr/`.

## License

AGPL-3.0-or-later. See `LICENSE`.
