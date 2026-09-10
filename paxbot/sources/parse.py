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


class _SkipRecord(Exception):
    """Internal: this one record cannot be parsed. Never escapes the module."""


@dataclass(frozen=True)
class SkippedRecord:
    raw_id: str | None
    reason: str


@dataclass
class ParseResult:
    events: list[Event] = field(default_factory=list)
    skipped: list[SkippedRecord] = field(default_factory=list)


def clean_text(raw: str | None) -> str:
    """Unescape entities, strip markup, collapse whitespace.

    Descriptions arrive with <br /> and titles/locations with &amp;.

    Unescaping happens FIRST so that an entity which decodes into markup
    (&lt;i&gt; -> <i>) is then stripped too. Stripping first would leave real
    tags in the output, which is exactly what this function promises to remove.
    """
    if not raw:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(raw))).strip()


def parse_schedules(payload: dict, show: Show) -> ParseResult:
    tz = ZoneInfo(show.timezone)
    result = ParseResult()

    for record in payload.get("schedules", []):
        gt_id = str(record.get("id") or "").strip()
        if not gt_id:
            result.skipped.append(SkippedRecord(None, "missing id"))
            continue

        try:
            event = _parse_record(record, gt_id, show, tz)
        except _SkipRecord as exc:
            result.skipped.append(SkippedRecord(gt_id, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad record must not
            # sink the whole payload. Upstream is a third party; a surprising
            # type in one field would otherwise lose every good record too.
            result.skipped.append(
                SkippedRecord(gt_id, f"unexpected {type(exc).__name__}: {exc}")
            )
            continue

        result.events.append(event)

    return result


def _parse_record(record: dict, gt_id: str, show: Show, tz: ZoneInfo) -> Event:
    """Parse one record. Raises _SkipRecord (or anything else) to reject it."""
    title = clean_text(record.get("title"))
    if not title:
        raise _SkipRecord("missing title")

    try:
        starts_local = datetime.strptime(record["start_time"], API_TIME_FORMAT)
        ends_local = datetime.strptime(record["end_time"], API_TIME_FORMAT)
    except (KeyError, TypeError, ValueError) as exc:
        raise _SkipRecord(f"unparseable start_time/end_time: {exc}") from exc

    # zoneinfo's correct idiom (unlike pytz, no .localize()). Known limitation:
    # for a local time inside a DST spring-forward gap or fall-back ambiguous
    # hour, fold defaults to 0 and the offset resolves silently, up to an hour
    # off. No configured PAX show spans a transition, so this is documented
    # rather than handled; revisit if a show is added that does.
    starts_local = starts_local.replace(tzinfo=tz)
    ends_local = ends_local.replace(tzinfo=tz)

    categories = tuple(
        name
        for name in (
            clean_text(c.get("name")) for c in record.get("schedule_categories") or []
        )
        if name
    )
    location = clean_text(record.get("location"))
    starts_utc = starts_local.astimezone(UTC)
    ends_utc = ends_local.astimezone(UTC)

    return (
        Event(
            show_slug=show.slug,
            gt_id=gt_id,
            title=title,
            description=clean_text(record.get("description")),
            starts_at=starts_utc,
            ends_at=ends_utc,
            day=starts_local.date(),
            location=location,
            url=f"{show.base_url}/en-us/schedule/schedule-item.html?gtID={gt_id}",
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
