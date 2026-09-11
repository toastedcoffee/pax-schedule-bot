"""Phase-1 CLI. Proves the data layer without Discord."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from paxbot.config import load_config
from paxbot.sources.leap import LeapError, fetch_schedules
from paxbot.store.db import connect
from paxbot.store.events import events_for_day
from paxbot.sync.diff import GuardRailError
from paxbot.sync.runner import run_sync

DEFAULT_DB = "paxbot.db"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "shows.toml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paxbot")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="fetch the schedule and update the store")
    sync.add_argument("--show", default="west")
    sync.add_argument("--db", default=DEFAULT_DB)

    listing = sub.add_parser("list", help="list events for a day")
    listing.add_argument("--day", required=True, help="YYYY-MM-DD")
    listing.add_argument("--category")
    listing.add_argument("--show", default="west")
    listing.add_argument("--db", default=DEFAULT_DB)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config(CONFIG_PATH)

    try:
        show = config.show(args.show)
    except KeyError as exc:
        print(str(exc).strip("\"'"), file=sys.stderr)
        return 2

    conn = connect(args.db)
    try:
        if args.command == "sync":
            return _cmd_sync(conn, show)
        return _cmd_list(conn, show, args, config.drop_in_threshold_minutes)
    finally:
        conn.close()


def _cmd_sync(conn, show) -> int:
    # Pass the fetcher explicitly: run_sync's own default is bound in its module,
    # so tests that patch paxbot.cli.fetch_schedules would otherwise hit the network.
    try:
        report = run_sync(conn, show, fetcher=fetch_schedules)
    except (LeapError, GuardRailError) as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"{show.slug}: added {report.added}, changed {report.changed}, "
        f"cancelled {report.removed}, unchanged {report.unchanged}, "
        f"skipped {report.skipped}"
    )
    return 0


def _cmd_list(conn, show, args, threshold: int) -> int:
    try:
        day = date.fromisoformat(args.day)
    except ValueError:
        print(f"invalid --day {args.day!r}, expected YYYY-MM-DD", file=sys.stderr)
        return 2

    tz = ZoneInfo(show.timezone)
    events = events_for_day(conn, show.slug, day, category=args.category)
    if not events:
        print(f"no events for {day} (have you run `paxbot sync`?)")
        return 0

    # ASCII only: Windows consoles default to cp1252, where an em dash renders
    # as a replacement character. This CLI is a diagnostic tool; typography is
    # not worth a portability bug on the platform it is self-hosted from.
    print(f"{show.name} - {day:%A, %B %d}")
    for event in events:
        start = event.starts_at.astimezone(tz)
        end = event.ends_at.astimezone(tz)
        when = f"{_clock(start)}-{_clock(end)}"
        marker = "  [drop-in]" if event.is_drop_in(threshold) else ""
        print(f"  {when:<18} {event.title}")
        print(f"  {'':<18} {event.location}{marker}")
    return 0


def _clock(moment) -> str:
    """'11:30am' — %-I is not portable to Windows, so strip the zero by hand."""
    return moment.strftime("%I:%M%p").lstrip("0").lower()
