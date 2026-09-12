# Architecture

paxbot has six layers. Each knows as little as possible about the others.

| Layer | Package | Knows | Must never import |
|---|---|---|---|
| Show adapters | `paxbot/sources/` | HTTP, JSON | `discord`, `sqlite3` |
| Sync | `paxbot/sync/` | sources, store | `discord` |
| Store | `paxbot/store/` | SQL | `httpx`, `discord` |
| View models | `paxbot/views.py` | store, config | `discord`, `httpx` |
| Bot | `paxbot/bot/` | Discord, views, store | `httpx` |
| Composition root | `paxbot/app.py` | everything | — |

`httpx` is the boundary that actually matters: it is what keeps fetching
confined to `sync/`, and that rule stands as written. `bot/` importing
`sqlite3` and reading the store directly (`paxbot/bot/commands.py` and
`paxbot/bot/panel.py` both do) is accepted, because `paxbot/store/` is
already the tested layer, not a fresh untested surface.

## Why

**`sources/` is isolated** so parsing is testable offline. Its tests load a frozen
JSON fixture — no network, no database, no bot token. Upstream changing its payload is
the most likely future breakage, so the code most likely to change has the fastest tests.

**`store/` is isolated** so swapping SQLite for another database touches one package.

**Sync sits between parse and write** so there is a point where a whole fetch exists but
has not been committed. That is where the guard rail stands.

**Command logic lives in plain functions** that Discord cogs call, so it can be tested
without a gateway connection.

## The rule that is easiest to break

Do not fetch or parse inside a command handler. Commands read from the store; the sync
job is the only thing that talks to the network. Putting an HTTP call in a cog means
re-fetching the whole schedule on every interaction, and makes the command untestable
without both a network connection and a bot token.

## Why there is a composition root

`app.py` is the only module that imports both `sync/` (which brings `httpx`) and
`bot/` (which brings `discord`). That is what makes the rule above structural rather
than advisory: a command handler *cannot* fetch, because `bot/` never imports anything
that can.

The periodic sync therefore lives in `app.py`, not in a cog. It runs through
`asyncio.to_thread` on its own SQLite connection — `sqlite3` connections are
single-threaded by default, and a 40-second fetch on the event loop would stall the
gateway heartbeat.

## Why view models are a layer

`views.py` takes a connection and returns plain dataclasses. It never imports
`discord`, which is what lets filtering, pagination, drop-in handling and conflict
detection all be tested offline — no gateway, no bot token. `bot/render.py` turns those
dataclasses into embeds and is equally pure, so Discord's silent limits (25 select
options, 6000 embed characters, 100-character `custom_id`s) are ordinary unit tests
instead of production surprises.

## Data source

The schedule comes from a single LEAP JSON endpoint:

    GET https://conventions.leapevent.tech/api/schedules?key=<api_key>

One request returns every event for a show. The `paxsite.com` pages are client-rendered
and contain no event data, so there is no HTML parsing anywhere in this project.

Per-show API keys live in `shows.toml`; `scripts/find_api_key.py` recovers one if it
rotates. Adding another PAX is a block in that file, not code.

### Two upstream quirks worth knowing

- **`no_end_time` means nothing.** It is `false` on some records and `null` on others,
  and both are falsy — the official site renders an end time for every event. Never
  branch on it.
- **Some events legitimately run all day.** Open-play zones and freeplay lounges have
  real end times spanning 8–14 hours. They are excluded from conflict checks by a
  duration threshold (see ADR 0004), not by any upstream flag.
