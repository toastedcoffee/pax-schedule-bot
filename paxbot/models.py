"""The vocabulary shared between layers. No HTTP, no SQL, no Discord."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime


def compute_row_hash(
    title: str,
    description: str,
    starts_at_iso: str,
    ends_at_iso: str,
    location: str,
    url: str,
    categories: tuple[str, ...],
) -> str:
    """Hash every user-visible field, so sync can name what actually changed.

    JSON-encoded rather than delimiter-joined: 32 of 713 real PAX West titles
    contain "|" and the category "Video Gaming (PC, HH, Console)" contains ",".
    A delimiter that occurs in the data lets field boundaries shift, so two
    different events could hash alike and a real change would read as unchanged.

    description and url are included deliberately: omitting them meant a
    description edited upstream could never reach the store.
    """
    payload = json.dumps(
        [title, description, starts_at_iso, ends_at_iso, location, url,
         sorted(categories)],
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

    # Defaulted so every existing constructor keeps working. Deliberately NOT
    # part of compute_row_hash: cancellation is our inference from an event
    # vanishing upstream, not a field the payload actually carries, and hashing
    # it would make a cancellation look like an upstream content change.
    cancelled: bool = False

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def is_drop_in(self, threshold_minutes: int) -> bool:
        """All-day open-play zones are not scheduling commitments.

        These have real end times; they are simply long. Treating a 13-hour
        freeplay zone as blocking would make it conflict with an entire day.
        """
        return self.duration_minutes >= threshold_minutes
