"""Event reads and writes."""
from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Iterable

from paxbot.models import Event


def _to_event(row: sqlite3.Row, categories: tuple[str, ...]) -> Event:
    return Event(
        show_slug=row["show_slug"],
        gt_id=row["gt_id"],
        title=row["title"],
        description=row["description"],
        starts_at=datetime.fromisoformat(row["starts_at"]),
        ends_at=datetime.fromisoformat(row["ends_at"]),
        day=date.fromisoformat(row["day"]),
        location=row["location"],
        url=row["url"],
        categories=categories,
        row_hash=row["row_hash"],
        cancelled=bool(row["cancelled"]),
    )


def _categories(conn, show_slug: str, gt_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT category FROM event_categories "
        "WHERE show_slug = ? AND gt_id = ? ORDER BY ordinal",
        (show_slug, gt_id),
    ).fetchall()
    return tuple(r["category"] for r in rows)


def upsert_events(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
    """Insert or update events and replace their categories.

    Must be called inside `transaction()`. The connection is in autocommit
    mode, so calling this bare would commit each statement independently,
    leaving a window where an event has no categories at all.
    """
    now = datetime.now(UTC).isoformat()
    for event in events:
        conn.execute(
            """
            INSERT INTO events (show_slug, gt_id, title, description, day,
                                starts_at, ends_at, location, url,
                                cancelled, row_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT (show_slug, gt_id) DO UPDATE SET
                title=excluded.title, description=excluded.description,
                day=excluded.day, starts_at=excluded.starts_at,
                ends_at=excluded.ends_at, location=excluded.location,
                url=excluded.url, cancelled=0,
                row_hash=excluded.row_hash, updated_at=excluded.updated_at
            """,
            (
                event.show_slug, event.gt_id, event.title, event.description,
                event.day.isoformat(), event.starts_at.isoformat(),
                event.ends_at.isoformat(), event.location, event.url,
                event.row_hash, now,
            ),
        )
        conn.execute(
            "DELETE FROM event_categories WHERE show_slug = ? AND gt_id = ?",
            (event.show_slug, event.gt_id),
        )
        conn.executemany(
            "INSERT INTO event_categories (show_slug, gt_id, category, ordinal) "
            "VALUES (?, ?, ?, ?)",
            [
                (event.show_slug, event.gt_id, c, i)
                for i, c in enumerate(event.categories)
            ],
        )


def mark_cancelled(conn, show_slug: str, gt_ids: Iterable[str]) -> None:
    """Retain the row. Deleting would silently drop it from saved schedules."""
    conn.executemany(
        "UPDATE events SET cancelled = 1 WHERE show_slug = ? AND gt_id = ?",
        [(show_slug, g) for g in gt_ids],
    )


def stored_hashes(conn, show_slug: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT gt_id, row_hash FROM events WHERE show_slug = ? AND cancelled = 0",
        (show_slug,),
    ).fetchall()
    return {r["gt_id"]: r["row_hash"] for r in rows}


def event_count(conn, show_slug: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE show_slug = ? AND cancelled = 0",
        (show_slug,),
    ).fetchone()["n"]


def get_event(conn, show_slug: str, gt_id: str) -> Event | None:
    row = conn.execute(
        "SELECT * FROM events WHERE show_slug = ? AND gt_id = ?", (show_slug, gt_id)
    ).fetchone()
    if row is None:
        return None
    return _to_event(row, _categories(conn, show_slug, gt_id))


def events_for_day(
    conn, show_slug: str, day: date, category: str | None = None
) -> list[Event]:
    sql = (
        "SELECT e.* FROM events e WHERE e.show_slug = ? AND e.day = ? "
        "AND e.cancelled = 0"
    )
    params: list[object] = [show_slug, day.isoformat()]
    if category:
        sql += (
            " AND EXISTS (SELECT 1 FROM event_categories c "
            "WHERE c.show_slug = e.show_slug AND c.gt_id = e.gt_id AND c.category = ?)"
        )
        params.append(category)
    sql += " ORDER BY e.starts_at, e.title, e.gt_id"
    rows = conn.execute(sql, params).fetchall()
    return [_to_event(r, _categories(conn, show_slug, r["gt_id"])) for r in rows]
