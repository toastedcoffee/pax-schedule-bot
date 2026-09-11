# Architecture

paxbot has four layers. Each knows as little as possible about the others.

| Layer | Package | Knows | Must never import |
|---|---|---|---|
| Show adapters | `paxbot/sources/` | HTTP, JSON | `discord`, `sqlite3` |
| Sync | `paxbot/sync/` | sources, store | `discord` |
| Store | `paxbot/store/` | SQL | `httpx`, `discord` |
| Bot (phase 2, not yet implemented) | `paxbot/bot/` | Discord, store | `httpx` |

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
