"""LEAP JSON payload -> Event objects.

Knows nothing about SQL or Discord. Every function here is pure, so the whole
module is testable offline against a frozen fixture.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from paxbot.config import Show
from paxbot.models import Event, compute_row_hash

API_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SkippedRecord:
    raw_id: str | None
    reason: str


@dataclass
class ParseResult:
    events: list[Event] = field(default_factory=list)
    skipped: list[SkippedRecord] = field(default_factory=list)


def clean_text(raw: str | None) -> str:
    """Strip markup, unescape entities, collapse whitespace.

    Descriptions arrive with <br /> and titles/locations with &amp;.
    """
    if not raw:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw))).strip()


def parse_schedules(payload: dict, show: Show) -> ParseResult:
    tz = ZoneInfo(show.timezone)
    result = ParseResult()

    for record in payload.get("schedules", []):
        gt_id = str(record.get("id") or "").strip()
        if not gt_id:
            result.skipped.append(SkippedRecord(None, "missing id"))
            continue

        title = clean_text(record.get("title"))
        if not title:
            result.skipped.append(SkippedRecord(gt_id, "missing title"))
            continue

        try:
            starts_local = datetime.strptime(record["start_time"], API_TIME_FORMAT)
            ends_local = datetime.strptime(record["end_time"], API_TIME_FORMAT)
        except (KeyError, TypeError, ValueError) as exc:
            result.skipped.append(
                SkippedRecord(gt_id, f"unparseable start_time/end_time: {exc}")
            )
            continue

        starts_local = starts_local.replace(tzinfo=tz)
        ends_local = ends_local.replace(tzinfo=tz)

        categories = tuple(
            clean_text(c.get("name"))
            for c in record.get("schedule_categories") or []
            if clean_text(c.get("name"))
        )
        location = clean_text(record.get("location"))
        starts_utc = starts_local.astimezone(UTC)
        ends_utc = ends_local.astimezone(UTC)

        result.events.append(
            Event(
                show_slug=show.slug,
                gt_id=gt_id,
                title=title,
                description=clean_text(record.get("description")),
                starts_at=starts_utc,
                ends_at=ends_utc,
                day=starts_local.date(),
                location=location,
                url=(
                    f"{show.base_url}/en-us/schedule/schedule-item.html?gtID={gt_id}"
                ),
                categories=categories,
                row_hash=compute_row_hash(
                    title,
                    starts_utc.isoformat(),
                    ends_utc.isoformat(),
                    location,
                    categories,
                ),
            )
        )

    return result
