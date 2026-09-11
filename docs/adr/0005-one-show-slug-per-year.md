# 0005 — One show slug per year, not one slug repointed annually

**Status:** Accepted

## Context

Every PAX convention recurs annually under one brand ("PAX West"), but each
year is a new LEAP payload behind a new API key. `paxbot/sync/diff.py`'s
guard rail refuses a sync whose event count falls below 70% of the last good
count for that slug - which is exactly what next year's freshly published,
still mostly-empty schedule looks like if it lands under the slug the
outgoing year used.

Two designs were on the table:

1. Repoint the existing slug (e.g. `[shows.west]`) at the new year in
   `shows.toml` each time the show recurs.
2. Give each year its own slug (`west-2026`, `west-2027`, ...) and never
   repoint one.

These are not distinguished by how the guard rail behaves: tested, a
rollover-sized drop trips it identically under either design ("got 1 events,
last good sync had 5"). Whatever decided this, it was not the guard rail.

## Decision

Each year's show gets its own slug in `shows.toml`. Adding a year means
adding a new `[shows.west-2027]` block, never editing `[shows.west-2026]` in
place.

## Consequences

- Repointing `west` at 2027's schedule would upsert an entirely different set
  of `gt_id`s under the same slug. `stored_hashes` for `west` would no longer
  contain any of this year's ids, `diff_events` would report all ~713 of them
  as removed, and `mark_cancelled` would flip every one to cancelled -
  permanently, since `store/events.py` never deletes a row. This year's
  schedule would read as 713 cancelled events forever, with no sync that
  undoes it.
- A new slug per year keeps last year's data addressable at its own slug
  indefinitely, which matters to anyone who saved events under it, and
  requires no schema change: `(show_slug, gt_id)` already scopes every event
  by show.
- This sidesteps the guard rail rather than relying on it: `west-2027`'s
  first sync is compared only against `west-2027`'s own (empty) history, so
  it never has to clear a threshold set by a different year's event count.
- The guard rail's own design is unrelated to this decision. Its comment in
  `sync/runner.py` used to justify a stateless ratchet by claiming a
  persisted high-water mark "would block rollover" - false, since both
  designs reject a rollover-sized drop the same way. The ratchet is kept
  because it needs no extra persisted state, not because of rollover; this
  ADR is what actually handles rollover, by not asking the guard rail to.
- The cost is manual bookkeeping: each new year needs a new `shows.toml`
  block and a new API key (`scripts/find_api_key.py`), and old slugs
  accumulate in the config file. With at most a handful of PAX shows a year,
  that is cheap.
