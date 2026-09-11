# 0002 — Schedules belong to users, visibility belongs to servers

**Status:** Accepted

## Context

The bot must work in more than one Discord server without leaking one server's data into
another, and without making a person rebuild their schedule per server.

## Decision

Saved events are keyed by `(user_id, show_slug)`. Sharing preferences are keyed by
`(guild_id, user_id)`.

## Consequences

- A person builds one schedule per show; it follows them into every server.
- Whether a server can see it is a per-server opt-in, default off.
- "Who else is going" queries filter to members who opted in *in that server*, so
  nothing crosses between servers.
