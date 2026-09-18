"""Composition root: the only module importing both sync (httpx) and bot
(discord).

This is what keeps "never fetch inside a command handler" a structural fact
rather than a convention - paxbot/bot/ never imports anything that can fetch.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

import discord
from discord.ext import tasks

from paxbot.bot.client import PaxClient
from paxbot.bot.commands import BotDeps
from paxbot.cli import _resolve_config, _resolve_db
from paxbot.config import Show, load_config
from paxbot.store.db import SchemaVersionError, connect
from paxbot.sync.policy import shows_to_sync
from paxbot.sync.runner import run_sync

log = logging.getLogger(__name__)

# The loop TICKS hourly year-round; the body decides whether enough time has
# passed to actually fetch. Simpler and safer than change_interval() plus
# restart(), which would restart the task from inside its own body, and it
# removes the "wait up to four hours for hourly polling to begin on day one"
# problem entirely. An hourly no-op wake costs nothing.
OFF_SEASON_HOURS = 4      # honours LEAP's max-age=14400
IN_SHOW_HOURS = 1


class StartupError(Exception):
    """The bot cannot start. Raised before connecting to Discord."""


def _database_hint(db_path: str) -> str:
    """Name the folder problem behind a database that will not open.

    SQLite reports a missing or unwritable folder only as "unable to open
    database file". Under Docker the usual cause is a bind-mount folder the
    daemon created itself: it runs as root, so the folder is root-owned no
    matter which user the container runs as.
    """
    folder = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(folder):
        return f"\n  {folder} does not exist."
    # SQLite also writes -wal and -shm files beside the database, so every one
    # that already exists must be writable too.
    stuck = [path for path in (db_path, f"{db_path}-wal", f"{db_path}-shm")
             if os.path.exists(path) and not os.access(path, os.W_OK)]
    if os.access(folder, os.W_OK) and not stuck:
        return ""
    uid = os.getuid() if hasattr(os, "getuid") else None
    who = f"uid {uid}" if uid is not None else "this user"
    unwritable = folder if not os.access(folder, os.W_OK) else ", ".join(stuck)
    return (
        f"\n  {unwritable} is not writable by {who}, which the bot runs as."
        "\n  Docker creates a missing bind-mount folder owned by root. On the "
        "host, chown -R the\n  data folder to the container's user (568:568 by "
        "default).")


def _warn_if_show_env_set() -> None:
    """Say so when a retired variable is set, rather than ignoring it silently.

    The live deployment sets PAXBOT_SHOW. A value that quietly does nothing is
    exactly the failure the database hint exists to prevent - so name it, say
    what replaced it, and say where the choice lives now.
    """
    if os.environ.get("PAXBOT_SHOW", "").strip():
        log.warning(
            "PAXBOT_SHOW=%s is set and ignored. shows.toml decides which "
            "shows this instance serves; /setup narrows it per server.",
            os.environ["PAXBOT_SHOW"].strip())


def _sync_due(db_path: str, shows: Sequence[Show]) -> None:
    """Sync every due show on ONE connection, in a worker thread.

    sqlite3 connections default to check_same_thread=True, so reusing the
    bot's connection from asyncio.to_thread would raise. This runs off the
    event loop precisely so a 40-second fetch cannot stall the gateway
    heartbeat - and with several shows that matters more, not less.

    Each show is isolated: one show whose upstream is broken, or whose payload
    trips the guard rail, must never cost the others their sync.
    """
    conn = connect(db_path)
    try:
        for show in shows:
            try:
                report = run_sync(conn, show)
                log.info("sync ok for %s: added %d changed %d cancelled %d",
                         show.slug, report.added, report.changed, report.removed)
            except Exception:
                log.exception("sync failed for %s; other shows continue",
                              show.slug)
    finally:
        conn.close()


def _last_ok_syncs(conn: sqlite3.Connection) -> dict[str, datetime]:
    """Every show's most recent SUCCESSFUL sync.

    A show with no entry has never synced successfully, which shows_to_sync
    reads as "poll it now" - that is the backfill path for a show added to
    shows.toml after it ended.
    """
    rows = conn.execute(
        "SELECT show_slug, MAX(ran_at) AS ran_at FROM sync_runs "
        "WHERE ok = 1 GROUP BY show_slug"
    ).fetchall()
    return {r["show_slug"]: datetime.fromisoformat(r["ran_at"]) for r in rows}


def build(argv=None) -> tuple[PaxClient, str, str]:
    """Validate everything, then build the client. Returns (client, token, db)."""
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise StartupError(
            "DISCORD_TOKEN is not set.\n"
            "  Docker:     put it in .env next to compose.yml, or in your stack "
            "manager's\n"
            "              environment variables (see .env.example).\n"
            "  Standalone: export it in your shell. .env is NOT read outside "
            "Docker - there is no python-dotenv dependency.")

    config_path = _resolve_config(None)
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise StartupError(f"cannot load config {config_path}: {exc}") from exc

    if not config.shows:
        raise StartupError(
            f"{config_path} defines no shows. Add a [shows.<slug>] block - "
            "see shows.toml in the repository for the shape.")
    _warn_if_show_env_set()

    db_path = _resolve_db(None)
    try:
        conn = connect(db_path)
        conn.execute("PRAGMA user_version")
    except (SchemaVersionError, sqlite3.Error) as exc:
        raise StartupError(f"cannot open database {db_path}: {exc}"
                           f"{_database_hint(db_path)}") from exc

    raw_guild = os.environ.get("PAXBOT_GUILD_ID", "").strip()
    try:
        guild_id = int(raw_guild) if raw_guild else None
    except ValueError as exc:
        raise StartupError(
            f"PAXBOT_GUILD_ID must be a number, got {raw_guild!r}") from exc

    deps = BotDeps(conn=conn, config=config)
    client = PaxClient(deps, guild_id)
    _attach_sync_loop(client, deps, db_path)
    return client, token, db_path


def _attach_sync_loop(client: PaxClient, deps: BotDeps, db_path: str) -> None:

    @tasks.loop(hours=IN_SHOW_HOURS)
    async def sync_loop():
        # This try/except is load-bearing and is the single most important
        # line in this module. discord.py's tasks.loop retries only a fixed
        # whitelist (OSError, GatewayNotFound, ConnectionClosed,
        # aiohttp.ClientError, asyncio.TimeoutError). Anything outside it
        # reaches the error handler, is logged, and then RE-RAISED, which
        # terminates the loop permanently - the bot keeps serving stale data
        # and never syncs again. LeapError, GuardRailError and sqlite3.Error
        # are all outside that whitelist, so the first guard-rail rejection
        # would end syncing for the life of the process, turning "degrade to
        # stale-but-correct" into "degrade to stale-forever-and-silent".
        try:
            now = datetime.now(UTC)
            due = shows_to_sync(
                tuple(deps.config.shows.values()),
                _last_ok_syncs(deps.conn),
                now, IN_SHOW_HOURS, OFF_SEASON_HOURS)
            if not due:
                # Also why every bot restart does not trigger a full fetch of
                # every show: tasks.loop runs its first iteration immediately.
                log.info("no shows due for sync")
                return
            await asyncio.to_thread(_sync_due, db_path, due)
        except Exception:
            log.exception("sync failed; the loop continues")

    @sync_loop.before_loop
    async def before():
        await client.wait_until_ready()

    client.sync_loop = sync_loop


def run(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("PAXBOT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        client, token, _ = build(argv)
    except StartupError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2

    try:
        # run() handles SIGINT/SIGTERM on POSIX, so `docker stop` shuts down
        # cleanly without extra signal plumbing.
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        print("Discord rejected DISCORD_TOKEN.", file=sys.stderr)
        return 2
    return 0
