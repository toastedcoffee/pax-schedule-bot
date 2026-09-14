"""The Discord client. Registers commands; owns no sync logic."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from paxbot.bot.commands import BotDeps, setup_commands

log = logging.getLogger(__name__)


class PaxClient(discord.Client):
    """The Discord client. Owns command registration and nothing else."""

    def __init__(self, deps: BotDeps, guild_id: int | None):
        # No message content or member intents: this bot only ever responds to
        # its own slash commands, and requesting privileged intents it does not
        # need would be both a review burden and a privacy overreach.
        super().__init__(intents=discord.Intents.default())
        self.deps = deps
        self.guild_id = guild_id
        self.tree = app_commands.CommandTree(self)
        # Set by app._attach_sync_loop before run(). Started in setup_hook
        # rather than by reassigning that method on the instance, which would
        # depend on discord.py resolving setup_hook off the instance.
        self.sync_loop = None

    async def setup_hook(self) -> None:
        setup_commands(self.tree, self.deps)
        self.tree.error(self._on_command_error)
        if self.sync_loop is not None:
            self.sync_loop.start()
        if self.guild_id is not None:
            # Guild-scoped commands register instantly; global ones can take
            # up to an hour to propagate.
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("commands synced to guild %s", self.guild_id)
        else:
            await self.tree.sync()
            log.info("commands synced globally (may take up to an hour)")

    async def _on_command_error(self, interaction: discord.Interaction,
                                error: Exception) -> None:
        """Answer a command whose body raised before responding.

        The views each handle their own callbacks, but a slash command body that
        raises has no such net: the interaction is simply never answered and
        Discord shows "the application did not respond" after three seconds,
        with nothing in the logs to say why.
        """
        log.exception("command failed", exc_info=error)
        note = "Something went wrong. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(note, ephemeral=True)
            else:
                await interaction.response.send_message(note, ephemeral=True)
        except discord.HTTPException:
            log.debug("could not report command failure", exc_info=True)
