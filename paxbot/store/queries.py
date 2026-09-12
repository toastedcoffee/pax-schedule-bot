"""Reads that span days: title search, upcoming events, category counts.

Window-by-hour filtering deliberately lives in views.py, not here: turning a
show-local day and hour into a UTC instant needs the show's timezone, and this
layer must not know about timezones.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from paxbot.models import Event
from paxbot.store.events import _categories, _to_event

# LIKE treats % and _ as wildcards, so user input carrying either would match
# far more than intended - a search for "100%" would otherwise return every
# event. Backslash is escaped first, or escaping the others would double-escape
# it. SQLite needs the escape character named explicitly via ESCAPE.
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _like_pattern(query: str) -> str:
    return f"%{query.translate(_LIKE_ESCAPE)}%"


def _rows_to_events(conn, show_slug: str, rows) -> list[Event]:
    return [_to_event(r, _categories(conn, show_slug, r["gt_id"])) for r in rows]


def search_events(
    conn: sqlite3.Connection,
    show_slug: str,
    query: str,
    limit: int = 25,
    *,
    day: date | None = None,
    after: datetime | None = None,
) -> list[Event]:
    """Title search, earliest first. `limit` defaults to Discord's 25-choice cap."""
    sql = ("SELECT * FROM events WHERE show_slug = ? AND cancelled = 0 "
           "AND title LIKE ? ESCAPE '\\'")
    params: list[object] = [show_slug, _like_pattern(query)]
    if day is not None:
        sql += " AND day = ?"
        params.append(day.isoformat())
    if after is not None:
        sql += " AND starts_at >= ?"
        params.append(after.isoformat())
    sql += " ORDER BY starts_at, title, gt_id LIMIT ?"
    params.append(limit)
    return _rows_to_events(conn, show_slug, conn.execute(sql, params).fetchall())


def count_matches_before(
    conn: sqlite3.Connection,
    show_slug: str,
    query: str,
    before: datetime,
    day: date | None = None,
) -> int:
    """How many matches were missed by a forward-only search.

    Powers the "No upcoming matches. 3 earlier today" hint, which prevents the
    one bad outcome of filtering by time: an event you know exists appearing
    not to.
    """
    sql = ("SELECT COUNT(*) AS n FROM events WHERE show_slug = ? AND cancelled = 0 "
           "AND title LIKE ? ESCAPE '\\' AND starts_at < ?")
    params: list[object] = [show_slug, _like_pattern(query), before.isoformat()]
    if day is not None:
        sql += " AND day = ?"
        params.append(day.isoformat())
    return conn.execute(sql, params).fetchone()["n"]


def upcoming_events(
    conn: sqlite3.Connection, show_slug: str, after: datetime, limit: int = 25
) -> list[Event]:
    """The next `limit` events starting at or after `after`.

    Fills /find's sub-2-character autocomplete slot, which Discord fires the
    moment the field is focused.
    """
    rows = conn.execute(
        "SELECT * FROM events WHERE show_slug = ? AND cancelled = 0 "
        "AND starts_at >= ? ORDER BY starts_at, title, gt_id LIMIT ?",
        (show_slug, after.isoformat(), limit),
    ).fetchall()
    return _rows_to_events(conn, show_slug, rows)


def distinct_categories(
    conn: sqlite3.Connection, show_slug: str
) -> list[tuple[str, int]]:
    """Category names with event counts, most frequent first.

    The panel's select can hold 24 of these plus an "All" option. Ordering by
    count is what makes that truncation cover 97.7% of assignments rather than
    an arbitrary alphabetical slice.
    """
    rows = conn.execute(
        "SELECT c.category, COUNT(*) AS n FROM event_categories c "
        "JOIN events e ON e.show_slug = c.show_slug AND e.gt_id = c.gt_id "
        "WHERE c.show_slug = ? AND e.cancelled = 0 "
        "GROUP BY c.category ORDER BY n DESC, c.category",
        (show_slug,),
    ).fetchall()
    return [(r["category"], r["n"]) for r in rows]
