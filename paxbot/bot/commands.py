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
from paxbot.bot.panel import ConfirmSave, SchedulePanel, _apologise
from paxbot.config import Config, Show
from paxbot.store.events import get_event
from paxbot.store.queries import (
    count_matches_before,
    distinct_categories,
    search_events,
    upcoming_events,
)
from paxbot.store.saved import save_event, saved_gt_ids, unsave_event
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
                    f"`{day[:32]}` is not a date in YYYY-MM-DD form.", ephemeral=True)
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
        # The command's response is the panel, so the command is its first
        # anchor. The panel replaces it with each click it answers in place;
        # this token alone would die 15 minutes after /schedule.
        panel.anchor = interaction

    @schedule.autocomplete("category")
    async def schedule_category_autocomplete(interaction: discord.Interaction,
                                             current: str):
        try:
            return _category_choices(deps, interaction, current)
        except Exception:
            log.exception("schedule category autocomplete failed")
            return []

    def _category_choices(deps: BotDeps, interaction: discord.Interaction,
                          current: str) -> list[app_commands.Choice]:
        # Unlike the panel's own select - capped at the top 24 by count, per
        # views.MAX_CATEGORY_OPTIONS - this reaches all 37: it is a plain text
        # argument, not a component with a 25-option ceiling, so a substring
        # match can surface any tail category the select cannot.
        show = deps.resolve_show(interaction.guild_id)
        cats = distinct_categories(deps.conn, show.slug)
        if current:
            needle = current.casefold()
            cats = [c for c in cats if needle in c[0].casefold()]
        return [
            app_commands.Choice(name=f"{name} ({count})", value=name)
            for name, count in cats[:AUTOCOMPLETE_LIMIT]
        ]

    @tree.command(name="find", description="Find an event by name")
    @app_commands.describe(query="At least 2 characters", day="YYYY-MM-DD")
    async def find(interaction: discord.Interaction,
                   query: app_commands.Range[str, 2, 100],
                   day: str | None = None):
        show = deps.resolve_show(interaction.guild_id)
        event = get_event(deps.conn, show.slug, query)
        if event is None:
            # `query` holds a gt_id when chosen from autocomplete; anything
            # else means the user typed and submitted free text.
            await _find_freetext(interaction, deps, show, query, day)
            return
        saved = saved_gt_ids(deps.conn, str(interaction.user.id), show.slug)
        is_saved = event.gt_id in saved
        await interaction.response.send_message(
            embed=render.event_embed(show, event,
                                     deps.config.drop_in_threshold_minutes,
                                     saved=is_saved),
            view=_FindResult(deps, show, event, saved=is_saved),
            ephemeral=True)

    @find.autocomplete("query")
    async def find_autocomplete(interaction: discord.Interaction, current: str):
        try:
            return _autocomplete_choices(deps, interaction, current)
        except Exception:
            # An autocomplete that raises shows an empty menu with no
            # explanation and Discord surfaces nothing. Degrade to no
            # suggestions, but leave a trace.
            log.exception("autocomplete failed")
            return []

    def _autocomplete_choices(
        deps: BotDeps, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
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
                    f"`{day[:32]}` is not a date in YYYY-MM-DD form.", ephemeral=True)
                return
        state = saved_view(deps.conn, show, str(interaction.user.id),
                           deps.config.drop_in_threshold_minutes, day=only)
        await interaction.response.send_message(
            embed=render.saved_embed(state,
                                     deps.config.drop_in_threshold_minutes),
            ephemeral=True)


def _show_start(show: Show) -> datetime:
    """Midnight on day one, in the show's timezone, converted to UTC.

    The conversion is load-bearing. The store compares `starts_at` as TEXT, so
    an ISO string carrying a -07:00 offset is byte-compared against one carrying
    +00:00 and the comparison silently means nothing. Verified: an event at
    2026-09-04T00:30:00+00:00 (17:30 local on the 3rd, before the show) sorts as
    >= "2026-09-04T00:00:00-07:00" and would be wrongly offered as upcoming.
    """
    return datetime(show.start_date.year, show.start_date.month,
                    show.start_date.day,
                    tzinfo=ZoneInfo(show.timezone)).astimezone(UTC)


async def _find_freetext(interaction, deps, show, query, day):
    now = deps.now()
    running = show.is_running(now)
    only = None
    if day:
        try:
            only = date.fromisoformat(day)
        except ValueError:
            await interaction.response.send_message(
                f"`{day[:32]}` is not a date in YYYY-MM-DD form.", ephemeral=True)
            return
    if len(query) < MIN_SEARCH_CHARS:
        await interaction.response.send_message(
            "Type at least 2 characters to search.", ephemeral=True)
        return

    results = search_events(deps.conn, show.slug, query, 10, day=only,
                            after=now if (running and only is None) else None)
    if results:
        # Budgeted in render.py: ten unbounded titles can exceed Discord's
        # 2000-character content cap, which it rejects outright rather than
        # truncating.
        await interaction.response.send_message(
            render.search_results_text(results, ZoneInfo(show.timezone)),
            ephemeral=True)
        return

    # The one bad outcome of filtering forward by default is an event you know
    # exists appearing not to. Say so rather than reporting nothing.
    missed = (count_matches_before(deps.conn, show.slug, query, now, only)
              if running and only is None else 0)
    hint = (f" {missed} earlier today — use `day:` to search the whole day."
            if missed else "")
    await interaction.response.send_message(
        f"No upcoming matches for “{query[:100]}”.{hint}", ephemeral=True)


class _FindResult(discord.ui.View):
    """A single event card with a star/unstar toggle button.

    /find is a one-off lookup with no fixed position like the panel's grid,
    so it cannot rely on the user navigating back to unstar - the button
    itself has to switch to "remove" once the event is already saved.
    """

    def __init__(self, deps: BotDeps, show: Show, event, saved: bool):
        super().__init__(timeout=840)
        self.deps = deps
        self.show = show
        self.event = event

        from paxbot.bot import ids
        if saved:
            button = discord.ui.Button(
                label="☆ Remove from my schedule",
                style=discord.ButtonStyle.secondary,
                custom_id=ids.encode(ids.UNSTAR, event.gt_id))
            button.callback = self._unsave
        else:
            button = discord.ui.Button(
                label="⭐ Add to my schedule",
                style=discord.ButtonStyle.primary,
                custom_id=ids.encode(ids.STAR, event.gt_id))
            button.callback = self._save
        self.add_item(button)

    async def _save(self, interaction: discord.Interaction):
        # Mirrors SchedulePanel._on_star: compute conflicts and only save when
        # there are none. Saving unconditionally and reporting "but X overlaps"
        # afterwards was a second, non-conformant conflict flow - one flow,
        # with Add anyway / Cancel, everywhere.
        threshold = self.deps.config.drop_in_threshold_minutes
        clashes = find_conflicts(self.deps.conn, self.show,
                                 str(interaction.user.id), self.event, threshold)
        if clashes:
            # Surfaced, never blocked: overlapping deliberately is legitimate.
            confirm = ConfirmSave(self.deps.conn, self.show,
                                  interaction.user.id, self.event.gt_id)
            await interaction.response.send_message(
                render.conflict_text(self.event, clashes,
                                     ZoneInfo(self.show.timezone)),
                view=confirm, ephemeral=True)
            try:
                confirm.message = await interaction.original_response()
            except discord.HTTPException:
                log.warning(
                    "could not capture confirm-save message; timeout will be silent",
                    exc_info=True)
            return
        save_event(self.deps.conn, str(interaction.user.id), self.show.slug,
                   self.event.gt_id)
        await interaction.response.send_message("Added.", ephemeral=True)

    async def _unsave(self, interaction: discord.Interaction):
        unsave_event(self.deps.conn, str(interaction.user.id), self.show.slug,
                    self.event.gt_id)
        await interaction.response.send_message("Removed.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item) -> None:
        """The save may already have committed before the failure, so say so
        rather than letting Discord claim the whole action failed."""
        log.exception("find-result interaction failed", exc_info=error)
        await _apologise(
            interaction,
            "Something went wrong finishing that. Check `/me` - it may have saved.")
