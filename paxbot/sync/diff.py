"""Compare a fetched payload against what is stored, and refuse bad syncs."""
from __future__ import annotations

from dataclasses import dataclass, field

from paxbot.models import Event

MIN_RETAINED_FRACTION = 0.7


class GuardRailError(Exception):
    """The fetch looks like an upstream breakage rather than real data."""


@dataclass
class SyncDiff:
    added: list[Event] = field(default_factory=list)
    changed: list[Event] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def diff_events(stored: dict[str, str], fetched: list[Event]) -> SyncDiff:
    diff = SyncDiff()
    seen: set[str] = set()

    for event in fetched:
        seen.add(event.gt_id)
        previous = stored.get(event.gt_id)
        if previous is None:
            diff.added.append(event)
        elif previous != event.row_hash:
            diff.changed.append(event)
        else:
            diff.unchanged += 1

    diff.removed = sorted(set(stored) - seen)
    return diff


def check_guard_rail(fetched_count: int, last_good_count: int) -> None:
    """Degrade to stale-but-correct, never to confidently-empty.

    A payload that suddenly loses most of its events is treated as a broken
    sync, not as a mass cancellation.
    """
    if fetched_count == 0:
        raise GuardRailError("refusing sync: parsed zero events")

    if last_good_count == 0:
        return

    if fetched_count < last_good_count * MIN_RETAINED_FRACTION:
        raise GuardRailError(
            f"refusing sync: got {fetched_count} events, "
            f"last good sync had {last_good_count} "
            f"(below {MIN_RETAINED_FRACTION:.0%} retention)"
        )
