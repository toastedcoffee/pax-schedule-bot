"""Guild-scoped configuration. Currently one table: which shows a guild follows.

Keyed by (guild_id, show_slug) because a guild follows a SET of shows - PAX
regulars attend several a year. A per-guild scalar setting does not fit that
key and belongs in a separate table; this module is the home for both.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from paxbot.store.db import transaction


def followed_slugs(conn: sqlite3.Connection, guild_id: str) -> tuple[str, ...] | None:
    """The shows this guild follows, or None if it has never been configured.

    None and an empty tuple are deliberately different. None means "follow
    everything, including shows added later" - the default that makes adding a
    show to shows.toml enough on its own. An empty tuple would mean "follow
    nothing", which no admin can express and the bot must never infer.
    """
    rows = conn.execute(
        "SELECT show_slug FROM guild_shows WHERE guild_id = ? ORDER BY show_slug",
        (guild_id,),
    ).fetchall()
    if not rows:
        return None
    return tuple(r["show_slug"] for r in rows)


def set_followed(conn: sqlite3.Connection, guild_id: str, slugs: Sequence[str],
                 set_by: str, now: datetime) -> None:
    """Replace this guild's follow set. All or nothing.

    A half-written set would leave the guild following a combination no admin
    chose, which resolves to a show nobody asked for.

    An empty set is refused: zero rows is how followed_slugs reports "never
    configured", so storing one would silently mean "follow everything".
    """
    if not slugs:
        raise ValueError("set_followed needs at least one show; use "
                         "clear_followed to return a guild to following everything")
    stamp = now.isoformat()
    with transaction(conn):
        conn.execute("DELETE FROM guild_shows WHERE guild_id = ?", (guild_id,))
        conn.executemany(
            "INSERT INTO guild_shows (guild_id, show_slug, set_by, set_at) "
            "VALUES (?, ?, ?, ?)",
            [(guild_id, slug, set_by, stamp) for slug in slugs],
        )


def clear_followed(conn: sqlite3.Connection, guild_id: str) -> None:
    """Return the guild to "follow everything, including shows added later"."""
    with transaction(conn):
        conn.execute("DELETE FROM guild_shows WHERE guild_id = ?", (guild_id,))
