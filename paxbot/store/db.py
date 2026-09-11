"""Connection and transaction handling. Knows SQL and nothing else."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from paxbot.store.schema import SCHEMA

SCHEMA_VERSION = 1


class TransactionError(Exception):
    """The connection is not in a state where a transaction can be opened."""


class SchemaVersionError(Exception):
    """The database was written by a newer paxbot than this one."""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to SCHEMA_VERSION.

    connect() runs CREATE TABLE IF NOT EXISTS before this, which does nothing
    to a table that already exists. Column additions therefore have to happen
    here, explicitly, before the version is stamped - stamping first would mark
    an unmigrated database as current and leave it permanently broken.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current == SCHEMA_VERSION:
        return
    if current > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database is at schema version {current}, this paxbot understands "
            f"{SCHEMA_VERSION} - upgrade paxbot or use a different database"
        )

    if current < 1 and "ordinal" not in _column_names(conn, "event_categories"):
        # Databases written before category ordering was preserved.
        conn.execute(
            "ALTER TABLE event_categories "
            "ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0"
        )

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection):
    """All-or-nothing. A failed sync must never leave partial data behind.

    Not reentrant: nesting would let an inner block commit work the outer one
    still intends to roll back. Nesting raises a named error rather than
    sqlite3's opaque "cannot start a transaction within a transaction".
    """
    if conn.in_transaction:
        raise TransactionError(
            "transaction() is not reentrant - a transaction is already open "
            "on this connection"
        )
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    try:
        conn.execute("COMMIT")
    except Exception:
        # A failed COMMIT would otherwise leave the transaction open, and every
        # later transaction() on this connection would fail at BEGIN instead.
        conn.execute("ROLLBACK")
        raise
