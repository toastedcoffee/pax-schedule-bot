# 0001 — AGPL-3.0

**Status:** Accepted

## Context

paxbot is self-hosted software that runs as a network service. The project should stay
open, including for anyone who hosts a modified copy for other people.

## Decision

License under AGPL-3.0-or-later.

## Consequences

- Anyone running a modified paxbot as a service must publish their changes. AGPL closes
  the network-service gap that makes GPL ineffective for hosted software, and a Discord
  bot is a network service by definition.
- Self-hosters who do not modify or redistribute are unaffected.
- The copyright holder is unrestricted by this choice and may relicense their own work.
