"""Constant-level guards for the command layer.

paxbot/bot/commands.py has no behavioural tests by project decision (spec §10),
but two of its constants encode hard Discord limits. Asserting them here means a
change that breaks one fails a test rather than silently truncating a menu.
"""
from paxbot.bot import commands


def test_autocomplete_limit_fits_discords_cap():
    """Discord returns at most 25 autocomplete choices and ignores the rest."""
    assert commands.AUTOCOMPLETE_LIMIT <= 25


def test_search_needs_at_least_two_characters():
    """One character against 713 titles is a random sample, not a search."""
    assert commands.MIN_SEARCH_CHARS >= 2
