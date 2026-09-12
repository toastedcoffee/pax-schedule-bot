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
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import tasks

from paxbot.bot.client import PaxClient
from paxbot.bot.commands import BotDeps
from paxbot.cli import _resolve_config, _resolve_db
from paxbot.config import load_config
from paxbot.store.db import connect
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


def _sync_once(db_path: str, show) -> None:
    """Run one sync on its OWN connection.

    sqlite3 connections default to check_same_thread=True, so reusing the
    bot's connection from asyncio.to_thread would raise. This runs in a worker
    thread precisely so a 40-second fetch cannot stall the gateway heartbeat.
    """
    conn = connect(db_path)
    try:
        report = run_sync(conn, show)
        log.info("sync ok: added %d changed %d cancelled %d",
                 report.added, report.changed, report.removed)
    finally:
        conn.close()


def _last_ok_sync(conn: sqlite3.Connection, show_slug: str) -> datetime | None:
    row = conn.execute(
        "SELECT ran_at FROM sync_runs WHERE show_slug = ? AND ok = 1 "
        "ORDER BY ran_at DESC LIMIT 1", (show_slug,)).fetchone()
    return datetime.fromisoformat(row["ran_at"]) if row else None


def build(argv=None) -> tuple[PaxClient, str, str]:
    """Validate everything, then build the client. Returns (client, token, db)."""
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise StartupError(
            "DISCORD_TOKEN is not set. Put it in .env (see .env.example); "
            "Compose loads it via env_file.")

    config_path = _resolve_config(None)
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise StartupError(f"cannot load config {config_path}: {exc}") from exc

    slug = os.environ.get("PAXBOT_SHOW", "west")
    try:
        show = config.show(slug)
    except KeyError as exc:
        raise StartupError(str(exc).strip("\"'")) from None

    db_path = _resolve_db(None)
    try:
        conn = connect(db_path)
        conn.execute("PRAGMA user_version")
    except sqlite3.Error as exc:
        raise StartupError(f"cannot open database {db_path}: {exc}") from exc

    raw_guild = os.environ.get("PAXBOT_GUILD_ID", "").strip()
    try:
        guild_id = int(raw_guild) if raw_guild else None
    except ValueError as exc:
        raise StartupError(
            f"PAXBOT_GUILD_ID must be a number, got {raw_guild!r}") from exc

    deps = BotDeps(conn=conn, config=config, show=show)
    client = PaxClient(deps, guild_id)
    _attach_sync_loop(client, deps, db_path)
    return client, token, db_path


def _attach_sync_loop(client: PaxClient, deps: BotDeps, db_path: str) -> None:

    @tasks.loop(hours=IN_SHOW_HOURS)
    async def sync_loop():
        # This try/except is load-bearing and is the single most important line
        # in this phase. discord.py's tasks.loop retries only a fixed whitelist
        # (OSError, GatewayNotFound, ConnectionClosed, aiohttp.ClientError,
        # asyncio.TimeoutError). Anything outside it reaches the error handler,
        # is logged, and then RE-RAISED, which terminates the loop permanently -
        # the bot keeps serving stale data and never syncs again. LeapError,
        # GuardRailError and sqlite3.Error are all outside that whitelist, so
        # the first guard-rail rejection would end syncing for the life of the
        # process, turning "degrade to stale-but-correct" into
        # "degrade to stale-forever-and-silent".
        try:
            now = datetime.now(UTC)
            wanted = (IN_SHOW_HOURS if deps.show.is_running(now)
                      else OFF_SEASON_HOURS)
            last = _last_ok_sync(deps.conn, deps.show.slug)
            if last is not None and now - last < timedelta(hours=wanted):
                # Also why every bot restart does not trigger a full 171 KB
                # fetch: tasks.loop runs its first iteration immediately.
                log.info("skipping sync: last good run was %s", last)
                return
            await asyncio.to_thread(_sync_once, db_path, deps.show)
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
