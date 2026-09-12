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
from paxbot.store.saved import saved_gt_ids

PAGE_SIZE = 6
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
    day_events = events_for_day(conn, show.slug, filters.day,
                                category=filters.category)
    tz = ZoneInfo(show.timezone)
    hours = tuple(sorted({e.starts_at.astimezone(tz).hour for e in day_events}))

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
