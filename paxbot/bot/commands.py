"""The three slash commands. Thin: they build a view model and render it.

No fetching, no parsing, no HTTP - see ARCHITECTURE.md. Commands read the
store; the sync job is the only thing that talks to the network.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from paxbot.bot import render
from paxbot.bot.panel import SchedulePanel
from paxbot.config import Config, Show
from paxbot.store.events import get_event
from paxbot.store.queries import count_matches_before, search_events, upcoming_events
from paxbot.store.saved import save_event, saved_gt_ids
from paxbot.views import default_filters, find_conflicts, saved_view

log = logging.getLogger(__name__)

# Two, because one character against 713 titles returns a random sample rather
# than a search. Discord fires autocomplete on focus, so the sub-2 slot answers
# a different question instead ("what's next") rather than sitting empty.
MIN_SEARCH_CHARS = 2
# Discord's hard cap on autocomplete choices. Passed straight to the store's
# LIMIT, so the choice list can never exceed it.
AUTOCOMPLETE_LIMIT = 25


@dataclass
class BotDeps:
    conn: sqlite3.Connection
    config: Config
    show: Show

    def now(self) -> datetime:
        return datetime.now(UTC)

    def resolve_show(self, guild_id: int | None) -> Show:
        """The ONLY place the show is chosen.

        Phase 2 ignores guild_id and returns the configured default. Phase 3
        replaces this body with a guild_config lookup and nothing else moves.
        PAX Unplugged in December makes that a dated requirement, not a
        hypothetical - which is why the seam exists now.
        """
        return self.show


def setup_commands(tree: app_commands.CommandTree, deps: BotDeps) -> None:

    @tree.command(name="schedule", description="Browse the schedule and build your own")
    @app_commands.describe(day="YYYY-MM-DD", category="Filter to one category")
    async def schedule(interaction: discord.Interaction,
                       day: str | None = None,
                       category: str | None = None):
        show = deps.resolve_show(interaction.guild_id)
        filters = default_filters(deps.conn, show, deps.now())
        if day:
            try:
                filters = type(filters)(day=date.fromisoformat(day),
                                        hour=None, category=category)
            except ValueError:
                await interaction.response.send_message(
                    f"`{day}` is not a date in YYYY-MM-DD form.", ephemeral=True)
                return
        elif category:
            filters = type(filters)(day=filters.day, hour=filters.hour,
                                    category=category)

        panel = SchedulePanel(
            conn=deps.conn, show=show, user_id=interaction.user.id,
            filters=filters,
            threshold_minutes=deps.config.drop_in_threshold_minutes,
            now_factory=deps.now,
        )
        await interaction.response.send_message(
            embed=panel.embed(), view=panel, ephemeral=True)
        panel.message = await interaction.original_response()

    @tree.command(name="find", description="Find an event by name")
    @app_commands.describe(query="At least 2 characters", day="YYYY-MM-DD")
    async def find(interaction: discord.Interaction, query: str,
                   day: str | None = None):
        show = deps.resolve_show(interaction.guild_id)
        event = get_event(deps.conn, show.slug, query)
        if event is None:
            # `query` holds a gt_id when chosen from autocomplete; anything
            # else means the user typed and submitted free text.
            await _find_freetext(interaction, deps, show, query, day)
            return
        saved = saved_gt_ids(deps.conn, str(interaction.user.id), show.slug)
        await interaction.response.send_message(
            embed=render.event_embed(show, event,
                                     deps.config.drop_in_threshold_minutes,
                                     saved=event.gt_id in saved),
            view=_FindResult(deps, show, event),
            ephemeral=True)

    @find.autocomplete("query")
    async def find_autocomplete(interaction: discord.Interaction, current: str):
        show = deps.resolve_show(interaction.guild_id)
        tz = ZoneInfo(show.timezone)
        now = deps.now()
        running = show.is_running(now)

        if len(current) < MIN_SEARCH_CHARS:
            # Discord fires autocomplete on focus, before anything is typed.
            # Answering "what's next" is more useful than an empty list.
            events = upcoming_events(
                deps.conn, show.slug,
                now if running else _show_start(show), AUTOCOMPLETE_LIMIT)
        else:
            events = search_events(
                deps.conn, show.slug, current, AUTOCOMPLETE_LIMIT,
                after=now if running else None)

        return [
            app_commands.Choice(name=render.autocomplete_label(e, tz),
                                value=e.gt_id)
            for e in events
        ]

    @tree.command(name="me", description="Your saved schedule")
    @app_commands.describe(day="YYYY-MM-DD")
    async def me(interaction: discord.Interaction, day: str | None = None):
        show = deps.resolve_show(interaction.guild_id)
        only = None
        if day:
            try:
                only = date.fromisoformat(day)
            except ValueError:
                await interaction.response.send_message(
                    f"`{day}` is not a date in YYYY-MM-DD form.", ephemeral=True)
                return
        state = saved_view(deps.conn, show, str(interaction.user.id),
                           deps.config.drop_in_threshold_minutes, day=only)
        await interaction.response.send_message(
            embed=render.saved_embed(state,
                                     deps.config.drop_in_threshold_minutes),
            ephemeral=True)


def _show_start(show: Show) -> datetime:
    return datetime(show.start_date.year, show.start_date.month,
                    show.start_date.day, tzinfo=ZoneInfo(show.timezone))


async def _find_freetext(interaction, deps, show, query, day):
    now = deps.now()
    running = show.is_running(now)
    only = None
    if day:
        try:
            only = date.fromisoformat(day)
        except ValueError:
            await interaction.response.send_message(
                f"`{day}` is not a date in YYYY-MM-DD form.", ephemeral=True)
            return
    if len(query) < MIN_SEARCH_CHARS:
        await interaction.response.send_message(
            "Type at least 2 characters to search.", ephemeral=True)
        return

    results = search_events(deps.conn, show.slug, query, 10, day=only,
                            after=now if (running and only is None) else None)
    if results:
        tz = ZoneInfo(show.timezone)
        lines = "\n".join(
            f"• {render.local_span(e, tz)} · {e.title}" for e in results)
        await interaction.response.send_message(
            f"**{len(results)} match(es)**\n{lines}", ephemeral=True)
        return

    # The one bad outcome of filtering forward by default is an event you know
    # exists appearing not to. Say so rather than reporting nothing.
    missed = (count_matches_before(deps.conn, show.slug, query, now, only)
              if running and only is None else 0)
    hint = (f" {missed} earlier today — use `day:` to search the whole day."
            if missed else "")
    await interaction.response.send_message(
        f"No upcoming matches for “{query}”.{hint}", ephemeral=True)


class _FindResult(discord.ui.View):
    """A single event card with a star button."""

    def __init__(self, deps: BotDeps, show: Show, event):
        super().__init__(timeout=840)
        self.deps = deps
        self.show = show
        self.event = event

        from paxbot.bot import ids
        button = discord.ui.Button(label="⭐ Add to my schedule",
                                   style=discord.ButtonStyle.primary,
                                   custom_id=ids.encode(ids.STAR, event.gt_id))
        button.callback = self._save
        self.add_item(button)

    async def _save(self, interaction: discord.Interaction):
        threshold = self.deps.config.drop_in_threshold_minutes
        clashes = find_conflicts(self.deps.conn, self.show,
                                 str(interaction.user.id), self.event, threshold)
        save_event(self.deps.conn, str(interaction.user.id), self.show.slug,
                   self.event.gt_id)
        if clashes:
            tz = ZoneInfo(self.show.timezone)
            first = clashes[0]
            await interaction.response.send_message(
                f"Added — but it overlaps **{first.title}**, "
                f"{render.local_span(first, tz)}.", ephemeral=True)
            return
        await interaction.response.send_message("Added.", ephemeral=True)
