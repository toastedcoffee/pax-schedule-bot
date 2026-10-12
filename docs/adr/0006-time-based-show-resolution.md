# 0006 — The current show is computed from the calendar, not configured

**Status:** Accepted

## Context

One instance of the bot can serve several PAX shows a year, and one Discord
server may care about several of them. Every command (`/schedule`, `/find`,
`/myschedule`) needs a single answer to "which show am I looking at?".

The obvious design is a stored setting: an environment variable per instance
(`PAXBOT_SHOW`, until now) or a "current show" per server. Either one is
correct on the day someone sets it and wrong from the day the next show starts.
Someone has to remember to flip it, and until they do the bot serves last
show's schedule with nothing to say it is stale.

Two upstream facts bound what the bot can do on its own:

- **A show cannot be discovered.** A show's schedule page carries no API key
  until ReedPop publishes that schedule, so there is nothing to find ahead of
  time.
- **A finished show's key keeps serving.** Its full payload is still returned
  months later, so a past show added to `shows.toml` can be backfilled.

## Decision

The active show is a function of the clock. For a given set of candidate
shows, the first tier with a match wins:

1. a show running now (earliest start wins);
2. a show that ended within `handover_grace_days` (default 14; latest end wins);
3. the soonest upcoming show;
4. the most recently ended show.

Ties inside a tier break on slug, so the answer is deterministic. Each show's
dates are judged in that show's own timezone. A show with no synced events is
not a candidate at any tier; if nothing is left, commands say there is no
schedule data yet.

The candidates are the shows a Discord server follows, stored as rows in
`guild_shows`. Zero rows means "follow every show in `shows.toml`, including
any added later". `/setup` (admin-only) narrows the set to specific shows, and
its **Follow every show** button returns to zero rows. DMs follow everything.

`shows.toml` decides which shows an instance serves; `/setup` narrows that per
server. `PAXBOT_SHOW` is retired: the bot logs a warning if it is still set, and
otherwise ignores it.

## Consequences

- No one has to remember to switch shows. Adding a show's `shows.toml` block
  and restarting is the whole transition; the bot syncs it and moves to it when
  the calendar says to.
- Adding a show is still a `shows.toml` edit. Because a show cannot be
  discovered before its schedule is published, automatic handover starts after
  that edit, never before it.
- **"Running" beats "upcoming"** because, during a show, the soonest upcoming
  show is a different one months away. A literal "next show" rule would move
  off the show people are standing in.
- **The grace window** keeps a just-finished show active for
  `handover_grace_days` (two weeks by default). The days after a show are when
  people compare what they saw and look back at their saved schedule; without
  it the bot would switch away the morning after closing.
- The grace window also gives the next show time to fill in. ReedPop publishes
  schedules progressively, and "published" can only mean "we have synced
  events for it" - a schedule with a dozen events meets that test. The grace
  window keeps a thin early schedule from replacing a full one straight away.
- A server that runs `/setup` and picks specific shows will not follow a show
  added later until an admin revisits `/setup`. A server that never runs it, or
  presses **Follow every show**, picks up new shows automatically.
- Saved events are never lost by a handover. `/myschedule show:` reaches any
  show the user has saves for, whichever show is active.
- The rule is a pure function (`paxbot/views.py`, `active_show`) taking the
  shows, the slugs with data, a clock and the grace days, so every case above
  is an offline unit test.
