"""Connection and transaction handling. Knows SQL and nothing else."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from paxbot.store.schema import SCHEMA


class TransactionError(Exception):
    """The connection is not in a state where a transaction can be opened."""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


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
