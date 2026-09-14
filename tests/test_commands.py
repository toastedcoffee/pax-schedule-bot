"""Guards for the command layer.

paxbot/bot/commands.py has no behavioural tests by project decision (spec §10).
What is here pins facts about the command SURFACE that break silently in
Discord rather than loudly in Python: two constants that encode hard Discord
limits, and the shape of what gets registered.
"""
import asyncio
import sqlite3
from pathlib import Path

import discord
from discord import app_commands

from paxbot.bot import commands
from paxbot.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]

# Discord's own slash commands, which it lists in the same picker as a bot's.
# Not exhaustive - Discord adds built-ins over time - but it covers the ones
# a person is likely to type. A bot command sharing one of these names means
# typing it quickly selects whichever entry happens to be highlighted.
DISCORD_BUILTINS = {
    "me", "shrug", "tableflip", "unflip", "spoiler", "nick", "tts", "msg",
    "thread", "giphy", "tenor", "kick", "ban", "timeout",
}


def _registered_tree():
    async def build():
        client = discord.Client(intents=discord.Intents.default())
        tree = app_commands.CommandTree(client)
        config = load_config(REPO_ROOT / "shows.toml")
        deps = commands.BotDeps(conn=sqlite3.connect(":memory:"),
                                config=config, show=config.show("west"))
        commands.setup_commands(tree, deps)
        await client.close()
        return tree

    return asyncio.run(build())


def test_autocomplete_limit_fits_discords_cap():
    """Discord returns at most 25 autocomplete choices and ignores the rest."""
    assert commands.AUTOCOMPLETE_LIMIT <= 25


def test_search_needs_at_least_two_characters():
    """One character against 713 titles is a random sample, not a search."""
    assert commands.MIN_SEARCH_CHARS >= 2


def test_no_command_shares_a_name_with_a_discord_builtin():
    """The saved-schedule command was once /me, which Discord's own /me action
    command shadows in the picker."""
    names = {command.name for command in _registered_tree().get_commands()}
    assert not names & DISCORD_BUILTINS


def test_every_day_option_offers_autocomplete():
    """Each registration is a hand-written line, so a new command taking a day
    could quietly ship without one and fall back to typing dates."""
    tree = _registered_tree()
    missing = [command.name for command in tree.get_commands()
               if "day" in command._params
               and command._params["day"].autocomplete is None]
    assert missing == []
