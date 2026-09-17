"""The guild follow set. Zero rows is the 'never configured' sentinel."""
from datetime import UTC, datetime

from paxbot.store.guilds import clear_followed, followed_slugs, set_followed

GUILD = "987654321098765432"
ADMIN = "123456789012345678"
NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def test_an_unconfigured_guild_reads_as_none_not_empty(conn):
    """None means 'follow everything'; an empty tuple would mean 'follow
    nothing', which no admin can express and the bot must never infer."""
    assert followed_slugs(conn, GUILD) is None


def test_a_set_follow_list_reads_back_sorted(conn):
    set_followed(conn, GUILD, ["west", "east"], ADMIN, NOW)
    assert followed_slugs(conn, GUILD) == ("east", "west")


def test_setting_replaces_rather_than_appends(conn):
    set_followed(conn, GUILD, ["west", "east"], ADMIN, NOW)
    set_followed(conn, GUILD, ["aus"], ADMIN, NOW)
    assert followed_slugs(conn, GUILD) == ("aus",)


def test_clearing_returns_the_guild_to_unconfigured(conn):
    set_followed(conn, GUILD, ["west"], ADMIN, NOW)
    clear_followed(conn, GUILD)
    assert followed_slugs(conn, GUILD) is None


def test_guilds_do_not_leak_into_each_other(conn):
    other = "111111111111111111"
    set_followed(conn, GUILD, ["west"], ADMIN, NOW)
    set_followed(conn, other, ["east"], ADMIN, NOW)
    assert followed_slugs(conn, GUILD) == ("west",)
    assert followed_slugs(conn, other) == ("east",)


def test_who_set_it_and_when_are_recorded(conn):
    set_followed(conn, GUILD, ["west"], ADMIN, NOW)
    row = conn.execute(
        "SELECT set_by, set_at FROM guild_shows WHERE guild_id = ?",
        (GUILD,)).fetchone()
    assert row["set_by"] == ADMIN
    assert row["set_at"] == NOW.isoformat()
