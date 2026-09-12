"""Saved-schedule reads and writes.

Keyed by (user_id, show_slug, gt_id): the schedule follows the person, not the
server (ADR 0002). Guild-scoped visibility is a separate phase 3 concern.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from paxbot.models import Event
from paxbot.store.events import _categories, _to_event


def save_event(conn: sqlite3.Connection, user_id: str, show_slug: str,
               gt_id: str) -> bool:
    """Save an event. Returns True if newly saved, False if already there.

    The caller uses the return value to distinguish "saved" from "you already
    had this", which are different messages to a user.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO saved (user_id, show_slug, gt_id, saved_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, show_slug, gt_id, datetime.now(UTC).isoformat()),
    )
    return cur.rowcount > 0


def unsave_event(conn: sqlite3.Connection, user_id: str, show_slug: str,
                 gt_id: str) -> bool:
    """Remove an event. Returns True if a row was actually removed."""
    cur = conn.execute(
        "DELETE FROM saved WHERE user_id = ? AND show_slug = ? AND gt_id = ?",
        (user_id, show_slug, gt_id),
    )
    return cur.rowcount > 0


def saved_gt_ids(conn: sqlite3.Connection, user_id: str,
                 show_slug: str) -> frozenset[str]:
    """Just the ids — used to render filled/empty stars without loading events."""
    rows = conn.execute(
        "SELECT gt_id FROM saved WHERE user_id = ? AND show_slug = ?",
        (user_id, show_slug),
    ).fetchall()
    return frozenset(r["gt_id"] for r in rows)


def saved_events(conn: sqlite3.Connection, user_id: str,
                 show_slug: str) -> list[Event]:
    """Saved events that still exist in the store, earliest first.

    Cancelled events ARE included: they must render marked, never vanish.
    Saved ids with no event row are not returned here - see missing_saved_ids.
    """
    rows = conn.execute(
        "SELECT e.* FROM saved s "
        "JOIN events e ON e.show_slug = s.show_slug AND e.gt_id = s.gt_id "
        "WHERE s.user_id = ? AND s.show_slug = ? "
        "ORDER BY e.starts_at, e.title, e.gt_id",
        (user_id, show_slug),
    ).fetchall()
    return [_to_event(r, _categories(conn, show_slug, r["gt_id"])) for r in rows]


def missing_saved_ids(conn: sqlite3.Connection, user_id: str,
                      show_slug: str) -> tuple[str, ...]:
    """Saved ids with no matching event row, so the UI can say 'removed'.

    Retaining the saved row and reporting it is deliberate: silently dropping
    an event from someone's schedule with no explanation is the failure mode
    this bot exists to fix.
    """
    rows = conn.execute(
        "SELECT s.gt_id FROM saved s "
        "LEFT JOIN events e ON e.show_slug = s.show_slug AND e.gt_id = s.gt_id "
        "WHERE s.user_id = ? AND s.show_slug = ? AND e.gt_id IS NULL "
        "ORDER BY s.gt_id",
        (user_id, show_slug),
    ).fetchall()
    return tuple(r["gt_id"] for r in rows)
