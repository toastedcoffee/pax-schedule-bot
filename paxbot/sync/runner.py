"""Orchestrate one sync: fetch -> parse -> guard -> write, atomically."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from paxbot.config import Show
from paxbot.sources.leap import fetch_schedules
from paxbot.sources.parse import parse_schedules
from paxbot.store.db import transaction
from paxbot.store.events import (
    event_count,
    mark_cancelled,
    stored_hashes,
    upsert_events,
)
from paxbot.sync.diff import GuardRailError, check_guard_rail, diff_events


@dataclass
class SyncReport:
    show_slug: str
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: int = 0
    ok: bool = False
    note: str = ""


def run_sync(conn: sqlite3.Connection, show: Show, fetcher=fetch_schedules) -> SyncReport:
    payload = fetcher(show)
    parsed = parse_schedules(payload, show)

    last_good = event_count(conn, show.slug)
    try:
        check_guard_rail(len(parsed.events), last_good)
    except GuardRailError as exc:
        _record_run(conn, show.slug, len(parsed.events), ok=False, note=str(exc))
        raise

    diff = diff_events(stored_hashes(conn, show.slug), parsed.events)

    with transaction(conn):
        upsert_events(conn, diff.added + diff.changed)
        if diff.removed:
            mark_cancelled(conn, show.slug, diff.removed)

    report = SyncReport(
        show_slug=show.slug,
        added=len(diff.added),
        changed=len(diff.changed),
        removed=len(diff.removed),
        unchanged=diff.unchanged,
        skipped=len(parsed.skipped),
        ok=True,
    )
    _record_run(conn, show.slug, len(parsed.events), ok=True, note="")
    return report


def _record_run(conn, show_slug: str, count: int, *, ok: bool, note: str) -> None:
    conn.execute(
        "INSERT INTO sync_runs (show_slug, ran_at, event_count, ok, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (show_slug, datetime.now().astimezone().isoformat(), count, int(ok), note),
    )
