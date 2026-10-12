"""The /setup panel: which shows this server follows.

Separate from panel.py because that module is the /schedule control panel and
is already at its five-action-row ceiling. It imports only the timeout
constant and the apology helper from there, but it deliberately mirrors two of
SchedulePanel's patterns, so a fix to either belongs in both:

- _render's all-or-nothing rollback of the children on a failed edit;
- the anchor. The expiry edit goes through the newest interaction whose
  response IS this panel, never through the /setup command's token. Every
  token dies 15 minutes after its own creation, while the 840s timeout counts
  from the last click, so on any panel clicked after about minute 1 an edit
  through the command's token fails - and the panel is left showing
  live-looking controls that answer "This interaction failed".
"""
from __future__ import annotations

import logging

import discord

from paxbot.bot import ids, render
from paxbot.bot.panel import PANEL_TIMEOUT_SECONDS, _apologise
from paxbot.store.guilds import clear_followed, followed_slugs, set_followed
from paxbot.store.queries import shows_with_data

log = logging.getLogger(__name__)


class SetupView(discord.ui.View):
    """A multi-select of configured shows plus a "Follow every show" button.

    The button is not decoration. A select cannot submit an empty set
    (min_values=1), so it is the only route back to the zero-rows state that
    means "follow everything, including shows added later" - the state that
    makes adding a block to shows.toml enough on its own.
    """

    def __init__(self, deps, guild_id: int, user_id: int):
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.deps = deps
        self.guild_id = guild_id
        self.user_id = user_id
        # The /setup command first, then each click answered by editing the
        # panel in place. on_timeout edits through this; see the module
        # docstring for why the command's token alone would not last.
        self.anchor: discord.Interaction | None = None
        self.rebuild()

    # ---- state -----------------------------------------------------------
    def _followed(self):
        return followed_slugs(self.deps.conn, str(self.guild_id))

    def embed(self) -> discord.Embed:
        shows = list(self.deps.config.shows.values())
        return render.setup_embed(shows, self._followed(),
                                  self.deps.resolve_show(self.guild_id))

    def rebuild(self) -> None:
        self.clear_items()
        shows = list(self.deps.config.shows.values())
        followed = self._followed()
        options = render.show_options(
            shows,
            # An unconfigured guild follows everything, so every option shows
            # as already selected - which is what it is.
            frozenset(followed) if followed is not None else frozenset(
                s.slug for s in shows),
            shows_with_data(self.deps.conn),
        )
        select = discord.ui.Select(
            custom_id=ids.encode(ids.SEL_SHOWS),
            placeholder="Shows this server follows",
            min_values=1,
            max_values=min(len(options), render.SELECT_OPTION_MAX),
            options=options,
            row=0,
        )
        select.callback = self._on_select
        self.add_item(select)

        button = discord.ui.Button(
            label="Follow every show",
            style=discord.ButtonStyle.secondary,
            custom_id=ids.encode(ids.FOLLOW_ALL),
            row=1,
        )
        button.callback = self._on_follow_all
        self.add_item(button)

    async def _render(self, interaction: discord.Interaction) -> None:
        """Rebuild the panel and answer the click with it, all or nothing.

        rebuild() detaches the live select and button - clear_items() sets
        each child's view to None - before the edit that would register their
        replacements in discord.py's view store. If that edit fails, the store
        still points at the detached items, and discord.py discards every later
        click on them as "View interaction referencing unknown view": the panel
        is dead, and unlike SchedulePanel there is no later successful click to
        heal it. So on any failure the previous children go back: the store
        references those exact objects, and re-adding them re-attaches them.

        Children are the whole of the rollback. SchedulePanel also restores its
        filters and state because it holds them across renders; nothing is
        missing here - this view keeps no per-render state, reading the follow
        set back out of the database on every rebuild.
        """
        previous = list(self.children)
        try:
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)
        except BaseException:
            self.clear_items()
            for child in previous:
                self.add_item(child)
            # Re-raised so on_error still answers the interaction; swallowing
            # it here would leave the click unanswered and Discord would show
            # a bare "This interaction failed" over a write that succeeded.
            # The anchor is not touched either: a failed click does not own
            # the message, so everything stays as it was.
            raise
        # Only a click answered in place owns the message, and its token -
        # good for 15 minutes from now - is the one the expiry edit must use.
        self.anchor = interaction

    # ---- callbacks -------------------------------------------------------
    async def _on_select(self, interaction: discord.Interaction) -> None:
        chosen = interaction.data["values"]
        set_followed(self.deps.conn, str(self.guild_id), chosen,
                     str(interaction.user.id), self.deps.now())
        await self._render(interaction)

    async def _on_follow_all(self, interaction: discord.Interaction) -> None:
        clear_followed(self.deps.conn, str(self.guild_id))
        await self._render(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "That panel belongs to someone else — run `/setup` yourself.",
            ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.anchor is None:
            return
        try:
            await self.anchor.edit_original_response(
                content="This panel expired — run `/setup` again.", view=self)
        except discord.HTTPException:
            # The anchor lags the last click only when that click failed, or
            # when a restart orphaned the panel. Then its token may be dead
            # and there is no surface left.
            log.debug("setup panel timeout edit failed", exc_info=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item) -> None:
        """The write may already have committed, so never claim it failed."""
        log.exception("setup interaction failed", exc_info=error)
        await _apologise(
            interaction,
            "Something went wrong saving that. Run `/setup` again to check "
            "what this server follows.")
