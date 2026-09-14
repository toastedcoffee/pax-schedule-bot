"""The /schedule control panel: one ephemeral message that rewrites itself.

Five action rows is Discord's hard maximum, and this uses all five:
  1 day select   2 hour select   3 category select
  4 Prev / Next / Search / Now / Reset
  5 six star buttons
There is no room for a sixth control. Anything new must replace something.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord

from paxbot.bot import ids, render
from paxbot.config import Show
from paxbot.store.events import get_event
from paxbot.store.queries import search_events
from paxbot.store.saved import save_event, unsave_event
from paxbot.views import PanelFilters, default_filters, find_conflicts, panel_view

log = logging.getLogger(__name__)

# discord.py measures this from the LAST click, while every interaction token
# dies 15 minutes after its OWN creation. Timing out at 14 therefore leaves a
# minute in which the most recent click's token can still carry the expiry
# edit - which is why out-of-band edits go through SchedulePanel.anchor and
# never through the /schedule command, whose token dies 15 minutes after the
# command however actively the panel is used.
PANEL_TIMEOUT_SECONDS = 840


async def _apologise(interaction: discord.Interaction, note: str) -> None:
    """Answer an interaction whose callback raised.

    Without this the interaction goes unanswered and Discord shows a bare
    "This interaction failed" three seconds later - even when the underlying
    write succeeded, which is the worst version of it: the user is told the
    action failed when it did not.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(note, ephemeral=True)
        else:
            await interaction.response.send_message(note, ephemeral=True)
    except discord.HTTPException:
        log.debug("could not report interaction failure", exc_info=True)


class SchedulePanel(discord.ui.View):
    def __init__(self, conn: sqlite3.Connection, show: Show, user_id: int,
                 filters: PanelFilters, threshold_minutes: int,
                 now_factory=lambda: datetime.now(tz=None).astimezone()):
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.conn = conn
        self.show = show
        self.user_id = user_id
        self.filters = filters
        self.threshold = threshold_minutes
        self.now_factory = now_factory
        # The newest interaction whose response IS this panel: the /schedule
        # command first, then each click answered by editing the panel in place.
        # Out-of-band edits (the expiry notice, the refresh after "Add anyway")
        # must use this. A stored panel message would carry the command's
        # token, which dies 15 minutes after /schedule even while the panel is
        # in active use.
        self.anchor: discord.Interaction | None = None
        self.state = self._load()
        self.rebuild()

    # ---- state -----------------------------------------------------------
    def _load(self):
        return panel_view(self.conn, self.show, self.filters, str(self.user_id),
                          self.threshold, self.now_factory())

    def embed(self) -> discord.Embed:
        return render.panel_embed(self.state)

    async def refresh(self, interaction: discord.Interaction,
                      filters: PanelFilters | None = None) -> None:
        """Reload and rewrite the message in place, answering the click.

        No defer(): reads are single-digit milliseconds and WAL means the sync
        job's writes never block them, so a direct edit is snappier than a
        loading state.
        """
        await self._render(interaction.response.edit_message, filters=filters)
        # This click was answered by editing the panel, so its token - good for
        # 15 minutes from now - is the one later out-of-band edits must use.
        self.anchor = interaction

    async def _render(self, edit, *, filters: PanelFilters | None = None) -> None:
        """Rebuild the panel and push it through `edit`, all or nothing.

        rebuild() detaches every existing button before the edit that would
        register their replacements in discord.py's view store. If that edit
        fails, the store keeps pointing at the detached buttons and every later
        click on the panel is discarded with "View interaction referencing
        unknown view" - the panel is dead for the rest of its life. So on any
        failure the previous filters, state and buttons are put back: the store
        still references those exact button objects, and re-adding them
        re-attaches them.
        """
        previous = (self.filters, self.state, list(self.children))
        if filters is not None:
            self.filters = filters
        try:
            self.state = self._load()
            self.rebuild()
            await edit(embed=self.embed(), view=self)
        except BaseException:
            self.filters, self.state, children = previous
            self.clear_items()
            for child in children:
                self.add_item(child)
            raise

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "That panel belongs to someone else — run `/schedule`.",
                ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item) -> None:
        """Answer the interaction even when a callback raised.

        An uncaught exception leaves the interaction unanswered, which Discord
        shows three seconds later as a bare "This interaction failed" with no
        explanation to the user and nothing useful in the logs. One handler here
        covers every callback on the view.
        """
        log.exception("panel interaction failed", exc_info=error)
        await _apologise(interaction, "Something went wrong. Run `/schedule` again.")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.anchor is None:
            return
        try:
            await self.anchor.edit_original_response(
                content="This panel expired. Run `/schedule` to open a new one.",
                view=self)
        except discord.HTTPException:
            # The anchor lags the last click only when that click was not
            # answered in place - opening Search, starring an event that has
            # since vanished, or an error - or when a restart orphaned the
            # panel. Then its token may be dead and there is no surface left.
            log.debug("panel timeout edit failed", exc_info=True)

    # ---- components ------------------------------------------------------
    def rebuild(self) -> None:
        self.clear_items()
        self._add_selects()
        self._add_nav()
        self._add_stars()

    def _add_selects(self) -> None:
        day = discord.ui.Select(custom_id=ids.encode(ids.SEL_DAY),
                                placeholder="Day", row=0,
                                options=render.day_options(self.show))
        day.callback = self._on_day
        self.add_item(day)

        hour = discord.ui.Select(custom_id=ids.encode(ids.SEL_HOUR),
                                 placeholder="Running at", row=1,
                                 options=render.hour_options(self.state))
        hour.callback = self._on_hour
        self.add_item(hour)

        cat = discord.ui.Select(custom_id=ids.encode(ids.SEL_CATEGORY),
                                placeholder="Category", row=2,
                                options=render.category_options(self.state))
        cat.callback = self._on_category
        self.add_item(cat)

    def _add_nav(self) -> None:
        specs = [
            (ids.PREV, "◀ Prev", self.state.page == 0),
            (ids.NEXT, "Next ▶",
             self.state.page >= self.state.page_count - 1),
            (ids.SEARCH, "\U0001f50d Search", False),
        ]
        if self.state.show_is_running:
            # "Now" is meaningless outside the show dates, so it is not rendered.
            specs.append((ids.NOW, "⏱ Now", False))
        specs.append((ids.RESET, "↺ Reset", False))

        for kind, label, disabled in specs:
            button = discord.ui.Button(
                label=label, custom_id=ids.encode(kind), row=3,
                disabled=disabled, style=discord.ButtonStyle.secondary)
            button.callback = self._on_nav
            self.add_item(button)

    def _add_stars(self) -> None:
        for index, event in enumerate(self.state.events, start=1):
            saved = event.gt_id in self.state.saved_ids
            # Bound outside the f-string: a backslash escape inside an
            # f-string expression is a syntax error before Python 3.12, and
            # this project supports 3.11.
            star = "⭐" if saved else "☆"
            button = discord.ui.Button(
                label=f"{star} {index}",
                custom_id=ids.encode(ids.UNSTAR if saved else ids.STAR,
                                     event.gt_id),
                row=4, style=discord.ButtonStyle.secondary)
            button.callback = self._on_star
            self.add_item(button)

    # ---- callbacks -------------------------------------------------------
    # Each callback hands its new filters to refresh() rather than assigning
    # self.filters first, so a failed edit can roll them back with the rest.
    async def _on_day(self, interaction: discord.Interaction) -> None:
        chosen = date.fromisoformat(interaction.data["values"][0])
        await self.refresh(interaction, PanelFilters(
            day=chosen, hour=None, category=self.filters.category, page=0))

    async def _on_hour(self, interaction: discord.Interaction) -> None:
        raw = interaction.data["values"][0]
        hour = None if raw == render.ALL_VALUE else int(raw)
        await self.refresh(interaction, PanelFilters(
            day=self.filters.day, hour=hour,
            category=self.filters.category, page=0))

    async def _on_category(self, interaction: discord.Interaction) -> None:
        raw = interaction.data["values"][0]
        category = None if raw == render.ALL_VALUE else raw
        await self.refresh(interaction, PanelFilters(
            day=self.filters.day, hour=self.filters.hour,
            category=category, page=0))

    async def _on_nav(self, interaction: discord.Interaction) -> None:
        kind, _ = ids.decode(interaction.data["custom_id"])
        if kind == ids.SEARCH:
            await interaction.response.send_modal(SearchModal(self))
            return
        filters = None
        if kind == ids.PREV:
            filters = _with_page(self.filters, self.filters.page - 1)
        elif kind == ids.NEXT:
            filters = _with_page(self.filters, self.filters.page + 1)
        elif kind == ids.RESET:
            filters = PanelFilters(day=self.show.start_date)
        elif kind == ids.NOW:
            filters = default_filters(self.conn, self.show, self.now_factory())
        await self.refresh(interaction, filters)

    async def _on_star(self, interaction: discord.Interaction) -> None:
        kind, gt_id = ids.decode(interaction.data["custom_id"])
        if kind == ids.UNSTAR:
            unsave_event(self.conn, str(self.user_id), self.show.slug, gt_id)
            await self.refresh(interaction)
            return

        event = get_event(self.conn, self.show.slug, gt_id)
        if event is None:
            await interaction.response.send_message(
                "That event is no longer in the schedule.", ephemeral=True)
            return

        clashes = find_conflicts(self.conn, self.show, str(self.user_id),
                                 event, self.threshold)
        if not clashes:
            save_event(self.conn, str(self.user_id), self.show.slug, gt_id)
            await self.refresh(interaction)
            return

        # Surfaced, never blocked: overlapping deliberately is legitimate.
        #
        # Answer the click by editing the panel, then prompt in a followup.
        # Answering with the prompt itself would make this click's original
        # response the prompt rather than the panel, so the click could not
        # serve as the anchor that "Add anyway" refreshes the panel through.
        await self.refresh(interaction)
        confirm = ConfirmSave(self.conn, self.show, self.user_id, gt_id, panel=self)
        # The followup's message edits through this click's token, which
        # outlives the prompt's own 14-minute timeout.
        confirm.message = await interaction.followup.send(
            render.conflict_text(event, clashes, ZoneInfo(self.show.timezone)),
            view=confirm, ephemeral=True, wait=True)


def _with_page(filters: PanelFilters, page: int) -> PanelFilters:
    return PanelFilters(day=filters.day, hour=filters.hour,
                        category=filters.category, page=max(0, page))


class ConfirmSave(discord.ui.View):
    """Add anyway / Cancel. Short-lived; it belongs to one decision.

    Usable with or without a parent panel: /find hits the same conflict
    prompt as /schedule's star button, but has no panel message to refresh.
    Timeout matches the panel's own - Discord kills the interaction token at
    15 minutes, and 840s leaves on_timeout a minute to still edit the message
    before the token itself would refuse the edit.
    """

    def __init__(self, conn, show: Show, user_id: int, gt_id: str,
                panel: "SchedulePanel | None" = None):
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.conn = conn
        self.show = show
        self.user_id = user_id
        self.gt_id = gt_id
        self.panel = panel
        self.message: discord.Message | None = None

        confirm = discord.ui.Button(
            label="Add anyway", style=discord.ButtonStyle.primary,
            custom_id=ids.encode(ids.CONFIRM, gt_id))
        confirm.callback = self._confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary,
            custom_id=ids.encode(ids.CANCEL, gt_id))
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        save_event(self.conn, str(self.user_id), self.show.slug, self.gt_id)
        panel = self.panel
        if panel is not None and panel.anchor is not None:
            # Out of band: this click belongs to the prompt, not the panel, so
            # the panel refreshes through its own newest click - the star click
            # that raised this prompt, or anything since. That token is always
            # under 15 minutes old here, because this prompt times out 14
            # minutes after it appears. _render rolls back on failure, so a
            # failed refresh costs the filled star, never the panel.
            try:
                await panel._render(panel.anchor.edit_original_response)
            except discord.HTTPException:
                log.debug("panel refresh after confirm failed", exc_info=True)
        await interaction.response.edit_message(content="Added.", view=None)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Not added.", view=None)

    async def on_timeout(self) -> None:
        """Discord drops the view after timeout regardless, leaving live-
        looking buttons that yield a bare "This interaction failed" - the same
        failure mode SchedulePanel.on_timeout exists to avoid."""
        for child in self.children:
            child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(
                content="This prompt expired — star the event again.",
                view=self)
        except discord.HTTPException:
            log.debug("confirm-save timeout edit failed", exc_info=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item) -> None:
        """The save may already have committed before the failure, so say so
        rather than letting Discord claim the whole action failed."""
        log.exception("confirm-save interaction failed", exc_info=error)
        await _apologise(
            interaction,
            "Something went wrong finishing that. Check `/me` - it may have saved.")


class SearchModal(discord.ui.Modal, title="Search the schedule"):
    """Free text, so it has no 25-option cap - the escape hatch for the 13
    categories that do not fit the select."""

    query = discord.ui.TextInput(label="Title contains", min_length=2,
                                 max_length=100, required=True)

    def __init__(self, panel: SchedulePanel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        results = search_events(self.panel.conn, self.panel.show.slug,
                                str(self.query), limit=10)
        if not results:
            await interaction.response.send_message(
                f"No events match “{self.query}”.", ephemeral=True)
            return
        tz = ZoneInfo(self.panel.show.timezone)
        await interaction.response.send_message(
            render.search_results_text(results, tz), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception) -> None:
        log.exception("search modal failed", exc_info=error)
        await _apologise(interaction, "Search failed. Try `/find` instead.")
