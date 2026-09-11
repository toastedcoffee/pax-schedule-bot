# 0003 — Frozen fixtures plus a separate live smoke test

**Status:** Accepted

## Context

Parsing depends on a third-party API that can change without notice. Tests that hit it
directly are flaky, need network, and cannot exercise malformed input on demand.

## Decision

Two test layers:

1. **Fixture tests** — offline, always run, against a small committed JSON payload plus
   hand-written malformed records.
2. **Live contract test** — marked `live`, deselected by default, asserting the real API
   still returns the fields we depend on.

## Consequences

- The default suite is fast, offline, and deterministic.
- Fixtures are trimmed to a handful of events: the repo tests a parser, it does not
  republish PAX's schedule.
- Upstream changes surface as a live-test failure, not as silent wrong behaviour.
- Fixture staleness is the point, not a defect. `scripts/refresh_fixtures.py` re-captures
  deliberately, so a payload change appears as a reviewable diff.
