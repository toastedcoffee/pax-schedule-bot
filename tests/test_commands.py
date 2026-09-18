"""Guards for the command layer.

paxbot/bot/commands.py has no behavioural tests by project decision (spec §10).
That decision covers the command bodies, which need a Discord gateway to drive;
BotDeps and the pure helpers beside it are ordinary code and are tested here.
Beyond those, what is here pins facts about the command SURFACE that break silently in
Discord rather than loudly in Python: two constants that encode hard Discord
limits, and the shape of what gets registered.
"""
import asyncio
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import discord
from discord import app_commands

from paxbot.bot import commands
from paxbot.bot.commands import BotDeps
from paxbot.config import Config, Show, load_config
from paxbot.store.db import transaction
from paxbot.store.events import upsert_events
from paxbot.store.guilds import followed_slugs, set_followed
from tests.conftest import make_event

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
        deps = commands.BotDeps(conn=sqlite3.connect(":memory:"), config=config)
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


def test_resolve_show_returns_none_when_nothing_has_data(conn):
    show = Show(slug="west", name="W", base_url="https://e.invalid", api_key="k",
                timezone="America/Los_Angeles",
                start_date=date(2026, 9, 4), end_date=date(2026, 9, 7))
    deps = BotDeps(conn=conn, config=Config(shows={"west": show},
                                            drop_in_threshold_minutes=240))
    assert deps.resolve_show(123) is None


def test_resolve_show_honours_a_guilds_follow_set(conn):

    def show(slug, start, end):
        return Show(slug=slug, name=slug, base_url="https://e.invalid",
                    api_key="k", timezone="America/Los_Angeles",
                    start_date=start, end_date=end)

    shows = {"west": show("west", date(2026, 9, 4), date(2026, 9, 7)),
             "east": show("east", date(2026, 3, 26), date(2026, 3, 29))}
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="1", show_slug="west"),
                             make_event(gt_id="2", show_slug="east")])
    set_followed(conn, "42", ["east"], "1", datetime(2026, 9, 16, tzinfo=UTC))

    deps = BotDeps(conn=conn, config=Config(shows=shows,
                                            drop_in_threshold_minutes=240))
    assert deps.resolve_show(42).slug == "east"
    # An unconfigured guild follows everything, so it gets the later show.
    assert deps.resolve_show(99).slug == "west"
    # A DM has no guild and follows everything too.
    assert deps.resolve_show(None).slug == "west"


def test_a_followed_slug_no_longer_in_config_is_ignored(conn):
    west = Show(slug="west", name="W", base_url="https://e.invalid", api_key="k",
                timezone="America/Los_Angeles",
                start_date=date(2026, 9, 4), end_date=date(2026, 9, 7))
    # Configured, with data, and NOT followed. On the pinned date it is the
    # upcoming show and would beat the long-ended west, so this only passes
    # while the follow set is actually applied.
    east = Show(slug="east", name="E", base_url="https://e.invalid", api_key="k",
                timezone="America/New_York",
                start_date=date(2027, 3, 25), end_date=date(2027, 3, 28))
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="1", show_slug="west"),
                             make_event(gt_id="2", show_slug="east")])
    set_followed(conn, "42", ["west", "gone"], "1", datetime(2026, 9, 16, tzinfo=UTC))
    deps = BotDeps(conn=conn, config=Config(shows={"west": west, "east": east},
                                            drop_in_threshold_minutes=240))
    # Pinned rather than the wall clock, so the tiers are known.
    deps.now = lambda: datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert deps.resolve_show(42).slug == "west"
    # Ignored, not deleted: the stale row survives until an admin re-runs
    # /setup, so restoring the show to shows.toml restores the follow.
    assert followed_slugs(conn, "42") == ("gone", "west")


def _fixed_show(slug, start, end):
    return Show(slug=slug, name=slug.upper(), base_url="https://e.invalid",
                api_key="k", timezone="America/Los_Angeles",
                start_date=start, end_date=end)


DAY_MENU_CONFIG = Config(
    shows={"west": _fixed_show("west", date(2026, 9, 4), date(2026, 9, 7)),
           "east": _fixed_show("east", date(2026, 3, 26), date(2026, 3, 29))},
    drop_in_threshold_minutes=240)


def test_day_menu_follows_an_explicit_show_argument():
    """/myschedule show:east must offer East's days, not the active show's -
    otherwise every suggested day fails with "not a day of PAX East"."""
    chosen = commands.show_for_day_menu(
        "east", DAY_MENU_CONFIG, lambda: DAY_MENU_CONFIG.shows["west"])
    assert chosen.slug == "east"


def test_day_menu_falls_back_to_the_active_show():
    """Commands with no show: parameter, and an unfilled one, see None."""
    west = DAY_MENU_CONFIG.shows["west"]
    assert commands.show_for_day_menu(None, DAY_MENU_CONFIG, lambda: west) is west
    assert commands.show_for_day_menu("", DAY_MENU_CONFIG, lambda: west) is west


def test_day_menu_ignores_a_show_argument_the_config_does_not_know():
    """A typed or stale slug gets the active show's days rather than nothing;
    the command itself reports the unknown show when submitted."""
    west = DAY_MENU_CONFIG.shows["west"]
    assert commands.show_for_day_menu("gone", DAY_MENU_CONFIG,
                                      lambda: west) is west


def test_day_menu_does_not_resolve_the_active_show_when_it_is_not_needed():
    def explode():
        raise AssertionError("fallback called")
    assert commands.show_for_day_menu("east", DAY_MENU_CONFIG,
                                      explode).slug == "east"


def test_saved_show_menu_skips_slugs_no_longer_in_config():
    """A save for a show since removed from shows.toml would be offered and
    then refused as "not a show this bot knows about"."""
    choices = commands.saved_show_choices(("east", "gone", "west"),
                                          DAY_MENU_CONFIG, "")
    assert [c.value for c in choices] == ["east", "west"]
    assert [c.name for c in choices] == ["EAST", "WEST"]


def test_saved_show_menu_narrows_by_name_or_slug():
    assert [c.value for c in commands.saved_show_choices(
        ("east", "west"), DAY_MENU_CONFIG, " We")] == ["west"]
