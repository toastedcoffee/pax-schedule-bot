"""The vocabulary shared between layers. No HTTP, no SQL, no Discord."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime


def compute_row_hash(
    title: str,
    starts_at_iso: str,
    ends_at_iso: str,
    location: str,
    categories: tuple[str, ...],
) -> str:
    """Hash the user-visible fields, so sync can name what actually changed.

    Encoded as JSON rather than delimiter-joined: real PAX titles contain
    "|" (every Magic: The Gathering event) and at least one real category
    contains "," ("Video Gaming (PC, HH, Console)"). A delimiter that can
    appear inside the data lets field boundaries shift, so two different
    events can flatten to the same payload and hash identically.
    """
    payload = json.dumps(
        [title, starts_at_iso, ends_at_iso, location, sorted(categories)],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    show_slug: str
    gt_id: str
    title: str
    description: str
    starts_at: datetime
    ends_at: datetime
    day: date
    location: str
    url: str
    categories: tuple[str, ...]
    row_hash: str

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def is_drop_in(self, threshold_minutes: int) -> bool:
        """All-day open-play zones are not scheduling commitments.

        These have real end times; they are simply long. Treating a 13-hour
        freeplay zone as blocking would make it conflict with an entire day.
        """
        return self.duration_minutes >= threshold_minutes
