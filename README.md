# pax-schedule-bot

Browse, build and share PAX convention schedules from Discord.

The official schedule is hard to search, tedious to turn into a personal plan, and
impossible to share except by screenshot. paxbot puts the whole schedule in Discord,
remembers what you picked, and lets you post an event your friends can add with one
click.

Self-hosted. Your data stays on your machine.

## Status

The data layer, sync job, CLI and Discord bot are all built and working. The
bot serves four slash commands — `/schedule`, `/find`, `/myschedule` and the
admin-only `/setup` — and syncs itself on a loop once it is running, so nothing
external needs to trigger a sync anymore. One instance serves every show in
`shows.toml` and picks the current one by date (see "Shows" below). The CLI's
`sync` and `list` subcommands are still there too, mainly for checking the
store directly without going through Discord.

In the Discord Developer Portal the application is registered as **Lanyard
Bot**; the package, repo and CLI all stay `paxbot`.

## Two ways to run it

Docker is the deployment target — a container on a Linux host (e.g. a TrueNAS
server) is the setup this project is built and tested against. A plain Python
install also works and is useful for development, or if you'd rather not run
Docker at all. Pick one.

### Option A: Docker (recommended)

`compose.yml` runs the bot (`paxbot bot`) as a long-running
`restart: unless-stopped` service from the published image,
`ghcr.io/toastedcoffee/pax-schedule-bot`. GitHub Actions rebuilds and
publishes that image on every change to `main`, tagged `latest` and
`sha-<commit>`. Nothing is built on your server, so the same file works pasted
into a stack manager, with no copy of the repo.

All settings are variables that `compose.yml` reads — see `.env.example` and
the tables below. Only `DISCORD_TOKEN` is required (Developer Portal -> your
application -> Bot -> Reset Token).

**The data directory must be writable by the container's user.** The container
runs as non-root uid/gid 568, which is TrueNAS SCALE's built-in `apps` user.
On TrueNAS, create a dataset for the data, apply the **Apps** permission
preset, and set `PAXBOT_DATA_DIR` to its path, e.g.
`/mnt/tank/apps/paxbot/data`. Elsewhere, Docker does not do this for you — a
directory Compose auto-creates for a bind mount comes back owned by root,
which uid 568 cannot write to, and the bot stops with `cannot open database`:

    mkdir -p ./data
    sudo chown 568:568 ./data

To run as a different host user, set `PAXBOT_UID` and `PAXBOT_GID` and `chown`
the directory to match.

#### With Dockge or Portainer (e.g. on TrueNAS)

1. Create a new stack and paste in `compose.yml`.
2. Add the variables: in Dockge, the stack's `.env` editor; in Portainer, the
   stack's environment variables. Set at least `DISCORD_TOKEN` and
   `PAXBOT_DATA_DIR`.
3. Deploy.

**To update**, pull the image and redeploy the stack (in Dockge, **Update**;
in Portainer, redeploy with **Re-pull image** on).

#### From a checkout

    cp .env.example .env        # then set DISCORD_TOKEN
    docker compose up -d        # start the bot
    docker compose pull && docker compose up -d   # update later

**Run a one-off command** (does not require the bot to be running; uses the
same image and data):

    docker compose run --rm paxbot sync --all
    docker compose run --rm paxbot list --day 2026-09-04 --category Panels

**Build from source** instead of pulling the published image — for example to
test a change before it merges:

    docker compose -f compose.yml -f compose.build.yml up -d --build

That override also mounts the checkout's `shows.toml`, so edits to it need no
rebuild.

#### Details

The database lives at `/data/paxbot.db` inside the container (set via the
`PAXBOT_DB` environment variable, which the CLI reads whenever `--db` is not
given explicitly). `PAXBOT_DATA_DIR` on the host, `./data` by default, is
mounted to `/data`.

The image ships `shows.toml`. To use your own copy — a new convention, or
changed dates — without waiting for a new image, mount it over the built-in
one; `compose.yml` has the line commented out:

    - /mnt/tank/apps/paxbot/shows.toml:/app/shows.toml:ro

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
the PAX schedule API and to Discord.

The bot syncs itself on a loop while it is running, so no external cron job
or scheduled task is needed to keep the schedule fresh. A cron entry running
`docker compose run --rm paxbot sync --all` is only useful if you want the CLI's
data kept current without running the bot at all.

### Option B: Standalone Python

No Docker. Requires Python 3.11+.

**Install:**

    python -m pip install -e ".[dev]"

**Run it as a module** — this is the invocation to use. A `paxbot` console
script is declared, but an editable install does not reliably put it on your
PATH; `python -m paxbot` always works regardless:

    python -m paxbot sync --all                        # fetch every show
    python -m paxbot sync --show west                  # fetch one show
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

### Bot environment variables

`paxbot bot` has no flags — everything comes from the environment.
`PAXBOT_DB` and `PAXBOT_CONFIG` above apply here too; both run paths resolve
the database and config the same way.

**Under Docker**, `compose.yml` passes these through from its variables: a
`.env` next to it, your stack manager's environment variables, or the shell
(see `.env.example`).

**Running standalone, `.env` is not read** — there is no `python-dotenv`
dependency, so set the variables in your shell:

    # PowerShell
    $env:DISCORD_TOKEN = "..."
    $env:PAXBOT_GUILD_ID = "..."

    # bash
    export DISCORD_TOKEN=... PAXBOT_GUILD_ID=...

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | — | Bot token from the Developer Portal (Bot -> Reset Token) |
| `PAXBOT_GUILD_ID` | no | none (registers globally) | Registers slash commands to one server instantly instead of waiting up to an hour for a global rollout |
| `PAXBOT_LOG_LEVEL` | no | `INFO` | Python logging level |
| `PAXBOT_DATA_DIR` | no | `./data` | Docker only: host directory mounted at `/data` for the database |
| `PAXBOT_UID` / `PAXBOT_GID` | no | `568` | Docker only: the uid/gid the container runs as (TrueNAS SCALE's `apps` user). Must own the data directory |
| `PAXBOT_TAG` | no | `latest` | Docker only: which published image tag to run; pin a `sha-<commit>` tag to hold a version |

**Upgrading:** `PAXBOT_SHOW` has been removed. If it is still set, the bot logs a
warning at startup and ignores it; delete it from your `.env` or stack.

## Shows

`shows.toml` lists every show this instance serves, one `[shows.<slug>]` block
each. There is no setting for "the current show": the bot works it out from
the calendar, picking the first match from

1. a show that is running now;
2. a show that ended within `handover_grace_days` (default 14, in
   `[defaults]`), so a finished show stays up while people look back at it;
3. the soonest upcoming show;
4. the most recently ended show.

Dates are judged in each show's own timezone, and a show with no synced events
yet is skipped. See `docs/adr/0006-time-based-show-resolution.md`.

The bot syncs every show that has not finished yet, plus any show that has
never synced, so a past show added to `shows.toml` is backfilled once. It
syncs hourly while a show is running and every four hours otherwise; one show
failing to sync does not hold up the others.

### Choosing shows per server

By default a Discord server follows every show in `shows.toml`, including any
added later. A server admin (anyone with **Manage Server**) can run `/setup`
to pick specific shows instead; the bot then chooses among those only. **Follow
every show** in the same message goes back to the default. A server that has
picked specific shows does not follow a show added later until an admin
returns to `/setup`. DMs always follow every show.

Saved events are kept per show. After the bot moves on to the next show,
`/myschedule show:` still reaches the saved schedule for any show you starred
events in.

## Adding another PAX

Add a block to `shows.toml`:

    [shows.east-2027]
    name       = "PAX East 2027"
    base_url   = "https://east.paxsite.com"
    api_key    = "..."
    timezone   = "America/New_York"
    start_date = 2027-04-21
    end_date   = 2027-04-25

Recover the API key with `python scripts/find_api_key.py east-2027`. The key only
exists once ReedPop has published that show's schedule, which is why the bot
cannot find new shows by itself: adding one is always this edit plus a restart.
A finished show's key keeps working, so a past show can be added too and its
schedule will be backfilled.

Under Docker, the published image picks up the change once it merges to
`main` and you update the stack. To use it sooner, mount your edited
`shows.toml` over the built-in one (see "Details" above).

### Annual rollover

Do not repoint an existing slug (e.g. `[shows.west]`) at next year's show. The
new year's schedule starts small and grows, and a repointed slug would make
this year's ~713 events look cancelled the moment the new one syncs in under
the same name - permanently, since events are never deleted, only marked
cancelled.

The shipped `[shows.west]` and `[shows.east]` blocks are the 2026 shows. Never
rename or repoint them: stored events and every user's saved schedule are keyed
by slug, so renaming `west` to `west-2026` would orphan all of them. Later
years get year-suffixed slugs, per ADR 0005:

    [shows.west]
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
address the new year, while `west`'s 2026 events stay in the store and
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
