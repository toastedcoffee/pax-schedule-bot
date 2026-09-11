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


def run_sync(conn: sqlite3.Connection, show: Show, fetcher=fetch_schedules) -> SyncReport:
    """Fetch, parse, guard, and write one sync atomically.

    Failures RAISE - they are never signalled by a returned SyncReport. A
    returned report therefore always describes a successful sync. Every
    failure path writes an ok=0 row to sync_runs before re-raising, so the
    audit table records attempts, not just successes.
    """
    try:
        payload = fetcher(show)
        parsed = parse_schedules(payload, show)
    except Exception as exc:
        _record_run(conn, show.slug, 0, ok=False, note=f"fetch/parse failed: {exc}")
        raise

    # NOTE: last_good is the current live count, so the threshold moves with it.
    # A series of individually-legitimate shrinkages can compound without any
    # single step tripping the guard. A persisted high-water mark was considered
    # and rejected: it would block the annual show rollover, when shows.toml is
    # repointed at next year's event and the freshly-published schedule is
    # legitimately tiny. Drift is better surfaced as a warning over sync_runs
    # history than as a gate that stops the bot updating at all.
    last_good = event_count(conn, show.slug)
    try:
        check_guard_rail(len(parsed.events), last_good)
    except GuardRailError as exc:
        _record_run(conn, show.slug, len(parsed.events), ok=False, note=str(exc))
        raise

    diff = diff_events(stored_hashes(conn, show.slug), parsed.events)

    try:
        with transaction(conn):
            upsert_events(conn, diff.added + diff.changed)
            if diff.removed:
                mark_cancelled(conn, show.slug, diff.removed)
    except Exception as exc:
        _record_run(conn, show.slug, len(parsed.events), ok=False, note=f"write failed: {exc}")
        raise

    report = SyncReport(
        show_slug=show.slug,
        added=len(diff.added),
        changed=len(diff.changed),
        removed=len(diff.removed),
        unchanged=diff.unchanged,
        skipped=len(parsed.skipped),
    )
    _record_run(conn, show.slug, len(parsed.events), ok=True, note="")
    return report


def _record_run(conn, show_slug: str, count: int, *, ok: bool, note: str) -> None:
    conn.execute(
        "INSERT INTO sync_runs (show_slug, ran_at, event_count, ok, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (show_slug, datetime.now().astimezone().isoformat(), count, int(ok), note),
    )
