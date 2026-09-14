"""Pure view models. Takes a connection, returns dataclasses.

This layer must never import `discord`. Everything the bot needs to decide -
filtering, pagination, drop-in handling, conflict detection - lives here, which
is what lets it all be tested offline with no gateway and no bot token.

Timezone policy also lives here rather than in store/: turning a show-local day
and hour into a UTC instant needs the show's timezone, and the store layer
knows only SQL.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from paxbot.config import Show
from paxbot.models import Event
from paxbot.store.events import events_for_day
from paxbot.store.queries import distinct_categories
from paxbot.store.saved import missing_saved_ids, saved_events, saved_gt_ids

# FIVE, not six. Every event on a page gets a star button and they all share one
# action row, which Discord caps at 5 buttons - discord.ui.View raises
# "item would not fit at row 4 (6 > 5 width)" on a full page. Raising this
# number makes the panel impossible to construct at all. Measured cost of 5 vs
# 6: the worst single hour on the real PAX West 2026 schedule holds 40 events,
# which is 8 pages instead of 7.
PAGE_SIZE = 5
# Discord caps a select at 25 options; one slot is spent on "All categories".
MAX_CATEGORY_OPTIONS = 24


@dataclass(frozen=True)
class PanelFilters:
    day: date
    hour: int | None = None       # None = the whole day
    category: str | None = None   # None = all categories
    page: int = 0


@dataclass(frozen=True)
class PanelState:
    show: Show
    filters: PanelFilters
    events: tuple[Event, ...]        # this page only, drop-ins excluded
    drop_ins: tuple[Event, ...]      # in-window drop-ins, never paged
    page: int                        # clamped
    page_count: int
    total: int                       # non-drop-in matches in the window
    saved_ids: frozenset[str]
    hours: tuple[int, ...]           # local hours this day actually spans
    categories: tuple[tuple[str, int], ...]
    show_is_running: bool


def _window(show: Show, day: date, hour: int | None) -> tuple[datetime, datetime]:
    """The [start, end) UTC instants for a show-local day, or one hour of it."""
    tz = ZoneInfo(show.timezone)
    if hour is None:
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz)
        return start, start + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
    return start, start + timedelta(hours=1)


def _overlaps(event: Event, start: datetime, end: datetime) -> bool:
    """Half-open: an event ending exactly at `start` is not in the window.

    Without this, every boundary would double-count and an event would appear
    in both the hour it ends and the hour after.
    """
    return event.starts_at < end and event.ends_at > start


def _spanned_hours(events, day: date, tz: ZoneInfo) -> tuple[int, ...]:
    """Every local hour of `day` in which at least one event is running.

    Spanned, not started. The panel filters by overlap, so the hour options
    have to be the hours something is actually running in. Deriving them from
    start times alone makes each day's final hour unreachable: on real PAX West
    2026 data nothing starts at 11pm on Friday or Saturday, yet events run
    until midnight, so "Running at 11pm" could never be selected.

    Capped implicitly at 24 entries by the day itself, so it can never overflow
    Discord's 25-option select even with the "All day" entry added.
    """
    hours: set[int] = set()
    for event in events:
        start = event.starts_at.astimezone(tz)
        end = event.ends_at.astimezone(tz)
        cursor = start.replace(minute=0, second=0, microsecond=0)
        while cursor < end:
            if cursor.date() == day:
                hours.add(cursor.hour)
            cursor += timedelta(hours=1)
    return tuple(sorted(hours))


def panel_view(
    conn: sqlite3.Connection,
    show: Show,
    filters: PanelFilters,
    user_id: str,
    threshold_minutes: int,
    now: datetime,
) -> PanelState:
    """Build everything the /schedule panel needs to render one screen.

    The whole day is loaded and filtered in Python rather than in SQL: the
    window needs the show's timezone, which the store layer must not know, and
    a day is at most ~206 rows.
    """
    tz = ZoneInfo(show.timezone)
    day_events = events_for_day(conn, show.slug, filters.day,
                                category=filters.category)

    # Hour options are computed from the WHOLE day, not the category-filtered
    # subset, so a sibling filter never shrinks them under the user. This is the
    # same rule `categories` already follows below - it draws from the whole
    # show rather than narrowing by the selected day or hour.
    unfiltered = (day_events if filters.category is None
                  else events_for_day(conn, show.slug, filters.day))
    hours = _spanned_hours(unfiltered, filters.day, tz)

    start, end = _window(show, filters.day, filters.hour)
    in_window = [e for e in day_events if _overlaps(e, start, end)]

    drop_ins = tuple(e for e in in_window if e.is_drop_in(threshold_minutes))
    paged = [e for e in in_window if not e.is_drop_in(threshold_minutes)]

    total = len(paged)
    page_count = max(1, -(-total // PAGE_SIZE))   # ceiling division
    # Clamp rather than trust: a stale panel can post a page number that no
    # longer exists, and an empty page beside a live Next button reads as a bug.
    page = max(0, min(filters.page, page_count - 1))
    window = tuple(paged[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])

    return PanelState(
        show=show,
        filters=filters,
        events=window,
        drop_ins=drop_ins,
        page=page,
        page_count=page_count,
        total=total,
        saved_ids=saved_gt_ids(conn, user_id, show.slug),
        hours=hours,
        categories=tuple(distinct_categories(conn, show.slug)[:MAX_CATEGORY_OPTIONS]),
        show_is_running=show.is_running(now),
    )


def default_filters(conn: sqlite3.Connection, show: Show,
                    now: datetime) -> PanelFilters:
    """During the show, open on today at the current hour. Otherwise day one.

    On the floor the useful first screen is "what is on right now". Before the
    show there is no meaningful "now", so the hour filter is left off and the
    whole of day one is shown.
    """
    if show.is_running(now):
        local = now.astimezone(ZoneInfo(show.timezone))
        return PanelFilters(day=local.date(), hour=local.hour)
    return PanelFilters(day=show.start_date, hour=None)


@dataclass(frozen=True)
class SavedDay:
    day: date
    events: tuple[Event, ...]
    conflict_ids: frozenset[str]


@dataclass(frozen=True)
class SavedState:
    show: Show
    days: tuple[SavedDay, ...]
    missing: tuple[str, ...]   # saved ids whose event no longer exists
    total: int


def find_conflicts(
    conn: sqlite3.Connection,
    show: Show,
    user_id: str,
    event: Event,
    threshold_minutes: int,
) -> tuple[Event, ...]:
    """Saved events overlapping `event`, earliest first. Empty means no clash.

    Drop-ins are excluded on BOTH sides. An all-day freeplay zone is a real
    event with a real end time, but not a scheduling commitment: letting one
    conflict with everything else that day would train users to click through
    warnings, destroying the feature that matters most.

    Cancelled events are excluded on both sides too. A cancelled event stays on
    a saved schedule and still renders, marked - but it is not happening, so
    warning that something "overlaps" it is noise about a slot that is in fact
    free.
    """
    if event.is_drop_in(threshold_minutes) or event.cancelled:
        return ()
    clashes = [
        other
        for other in saved_events(conn, user_id, show.slug)
        if other.gt_id != event.gt_id
        and not other.cancelled
        and not other.is_drop_in(threshold_minutes)
        and _overlaps(other, event.starts_at, event.ends_at)
    ]
    return tuple(clashes)


def saved_view(
    conn: sqlite3.Connection,
    show: Show,
    user_id: str,
    threshold_minutes: int,
    day: date | None = None,
) -> SavedState:
    """The /myschedule view, grouped by day with conflicts flagged in place."""
    events = saved_events(conn, user_id, show.slug)
    if day is not None:
        events = [e for e in events if e.day == day]

    by_day: dict[date, list[Event]] = {}
    for event in events:
        by_day.setdefault(event.day, []).append(event)

    days = []
    for d in sorted(by_day):
        same_day = by_day[d]
        # Cancelled events still render (marked), but cannot clash with
        # anything - the slot they occupied is free.
        blocking = [e for e in same_day
                    if not e.is_drop_in(threshold_minutes) and not e.cancelled]
        # Both sides of a clashing pair are flagged, so /myschedule marks the two
        # events that overlap rather than only the later one.
        clashing: set[str] = set()
        for i, first in enumerate(blocking):
            for second in blocking[i + 1:]:
                if _overlaps(first, second.starts_at, second.ends_at):
                    clashing.add(first.gt_id)
                    clashing.add(second.gt_id)
        days.append(SavedDay(day=d, events=tuple(same_day),
                             conflict_ids=frozenset(clashing)))

    return SavedState(
        show=show,
        days=tuple(days),
        missing=missing_saved_ids(conn, user_id, show.slug),
        total=len(events),
    )
