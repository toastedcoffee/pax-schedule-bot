"""The /setup panel: which shows this server follows.

Separate from panel.py because that module is the /schedule control panel and
is already at its five-action-row ceiling; this shares nothing with it but the
timeout constant and the apology helper.
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
        self.message: discord.Message | None = None
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
            max_values=min(len(options), render.MAX_SHOW_OPTIONS),
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
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

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
        if self.message is None:
            return
        try:
            await self.message.edit(
                content="This panel expired — run `/setup` again.", view=self)
        except discord.HTTPException:
            log.debug("setup panel timeout edit failed", exc_info=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item) -> None:
        """The write may already have committed, so never claim it failed."""
        log.exception("setup interaction failed", exc_info=error)
        await _apologise(
            interaction,
            "Something went wrong saving that. Run `/setup` again to check "
            "what this server follows.")
