# 0004 — Long events are drop-ins and never raise conflicts

**Status:** Accepted

## Context

Conflict warnings are the feature most likely to be trusted, so false positives are
expensive. Some events run 8–14 hours: open-play zones and freeplay lounges. Their end
times are real, but they are not scheduling commitments.

The upstream `no_end_time` field does not identify them — it is falsy on every record
and distinguishes nothing.

## Decision

An event whose duration is at least `drop_in_threshold_minutes` (default 240) is treated
as a drop-in: excluded from conflict checks on both sides, and labelled as all-day in
listings.

Derived at query time from `starts_at`/`ends_at`, never stored, so retuning the threshold
needs no re-sync.

## Consequences

- Saving an all-day open-play zone does not make every other event that day conflict.
- This is a heuristic, and it is documented as one. A genuinely long single-session event
  would be misclassified; that is preferable to warnings users learn to ignore.
- The threshold is configurable per deployment.
