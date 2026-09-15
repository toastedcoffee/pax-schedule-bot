from datetime import UTC, date, datetime

from paxbot.store.db import transaction
from paxbot.store.events import upsert_events
from paxbot.store.queries import (
    count_matches_before,
    distinct_categories,
    match_events,
    search_events,
    upcoming_events,
)
from tests.conftest import make_event


def seed(conn, events):
    with transaction(conn):
        upsert_events(conn, events)


def test_search_matches_case_insensitively(conn):
    seed(conn, [make_event(gt_id="1", title="Indie Game Showcase")])
    assert [e.gt_id for e in search_events(conn, "west", "indie")] == ["1"]
    assert [e.gt_id for e in search_events(conn, "west", "SHOWCASE")] == ["1"]


def test_search_matches_mid_string(conn):
    seed(conn, [make_event(gt_id="1", title="The Omegathon Finals")])
    assert [e.gt_id for e in search_events(conn, "west", "megath")] == ["1"]


def test_search_escapes_percent(conn):
    """A bare % in user input must match a literal %, not everything.

    The control title must itself contain "100" but no literal "%". With a
    control like "Something Else" this test is VACUOUS: the naive pattern
    "%100%%" fails to match it anyway, so the assertion passes with or without
    escaping. "1000 Blank White Cards" is matched by the naive pattern and
    rejected by the escaped one, which is what makes the test discriminate.
    """
    seed(conn, [
        make_event(gt_id="1", title="100% Orange Juice"),
        make_event(gt_id="2", title="1000 Blank White Cards"),
    ])
    assert [e.gt_id for e in search_events(conn, "west", "100%")] == ["1"]


def test_search_escapes_underscore(conn):
    """_ is a single-character wildcard in LIKE and must be escaped."""
    seed(conn, [
        make_event(gt_id="1", title="snake_case Panel"),
        make_event(gt_id="2", title="snakeXcase Panel"),
    ])
    assert [e.gt_id for e in search_events(conn, "west", "snake_case")] == ["1"]


def test_search_escapes_backslash(conn):
    seed(conn, [make_event(gt_id="1", title="A\\B Panel")])
    assert [e.gt_id for e in search_events(conn, "west", "A\\B")] == ["1"]


def test_search_respects_limit(conn):
    seed(conn, [make_event(gt_id=str(i), title=f"Panel {i}", hour=10)
                for i in range(40)])
    assert len(search_events(conn, "west", "Panel", limit=25)) == 25


def test_search_excludes_cancelled(conn):
    from paxbot.store.events import mark_cancelled
    seed(conn, [make_event(gt_id="1", title="Gone Panel")])
    with transaction(conn):
        mark_cancelled(conn, "west", ["1"])
    assert search_events(conn, "west", "Gone") == []


def test_search_filters_by_day(conn):
    seed(conn, [
        make_event(gt_id="fri", title="Board Games", day=date(2026, 9, 4)),
        make_event(gt_id="sat", title="Board Games", day=date(2026, 9, 5)),
    ])
    got = search_events(conn, "west", "Board", day=date(2026, 9, 5))
    assert [e.gt_id for e in got] == ["sat"]


def test_search_filters_from_a_moment_forward(conn):
    seed(conn, [
        make_event(gt_id="early", title="Board Games", hour=10),
        make_event(gt_id="late", title="Board Games", hour=20),
    ])
    after = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
    assert [e.gt_id for e in search_events(conn, "west", "Board", after=after)] == ["late"]


def test_search_is_scoped_by_show(conn):
    seed(conn, [
        make_event(gt_id="1", title="Board Games", show_slug="west"),
        make_event(gt_id="2", title="Board Games", show_slug="unplugged"),
    ])
    assert [e.gt_id for e in search_events(conn, "west", "Board")] == ["1"]


def test_upcoming_is_ordered_and_limited(conn):
    seed(conn, [make_event(gt_id=str(h), hour=h) for h in (9, 11, 13, 15)])
    got = upcoming_events(conn, "west", datetime(2026, 9, 4, 10, 0, tzinfo=UTC), limit=2)
    assert [e.gt_id for e in got] == ["11", "13"]


def test_upcoming_includes_an_event_starting_exactly_now(conn):
    seed(conn, [make_event(gt_id="now", hour=12)])
    at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    assert [e.gt_id for e in upcoming_events(conn, "west", at)] == ["now"]


def test_distinct_categories_counts_and_orders(conn):
    seed(conn, [
        make_event(gt_id="1", categories=("Tabletop", "TCGs")),
        make_event(gt_id="2", categories=("Tabletop",)),
        make_event(gt_id="3", categories=("Panels",)),
    ])
    assert distinct_categories(conn, "west")[0] == ("Tabletop", 2)
    assert set(distinct_categories(conn, "west")) == {
        ("Tabletop", 2), ("TCGs", 1), ("Panels", 1)}


def test_count_matches_before_powers_the_earlier_hint(conn):
    seed(conn, [
        make_event(gt_id="a", title="Board Games", hour=9),
        make_event(gt_id="b", title="Board Games", hour=10),
        make_event(gt_id="c", title="Board Games", hour=20),
    ])
    before = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
    assert count_matches_before(conn, "west", "Board", before) == 2


def test_count_matches_before_scopes_to_one_day(conn):
    """The hint is day-scoped when /find's own day filter is set, so a match
    on another day must not inflate the count."""
    fri = date(2026, 9, 4)
    sat = date(2026, 9, 5)
    seed(conn, [
        make_event(gt_id="a", title="Board Games", hour=9, day=fri),
        make_event(gt_id="b", title="Board Games", hour=10, day=fri),
        make_event(gt_id="c", title="Board Games", hour=9, day=sat),
    ])
    before = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
    assert count_matches_before(conn, "west", "Board", before, day=fri) == 2
    assert count_matches_before(conn, "west", "Board", before, day=sat) == 1


# ---- match_events: the panel's Search, titles AND category names ----------

def test_match_finds_an_event_by_category_name_alone(conn):
    """Search is the escape hatch for the categories the select cannot fit, so
    a category name must find events whose titles never mention it."""
    seed(conn, [
        make_event(gt_id="1", title="Crokinole Open", categories=("Tabletop Tournaments",)),
        make_event(gt_id="2", title="Something Else", categories=("Panels",)),
    ])
    assert [e.gt_id for e in match_events(conn, "west", "tournament")] == ["1"]


def test_match_returns_an_event_once_when_title_and_category_both_match(conn):
    seed(conn, [make_event(gt_id="1", title="Tabletop Social",
                           categories=("Tabletop", "Tabletop Freeplay"))])
    assert [e.gt_id for e in match_events(conn, "west", "tabletop")] == ["1"]


def test_match_escapes_wildcards_in_the_category_clause_too(conn):
    """An underscore must not act as a one-character wildcard on categories."""
    seed(conn, [make_event(gt_id="1", title="Plain", categories=("AxB",))])
    assert match_events(conn, "west", "A_B") == []


def test_match_is_not_capped_at_discords_choice_limit(conn):
    """The panel pages its own results; truncating at 25 would hide events."""
    seed(conn, [make_event(gt_id=str(i), title=f"Panel {i}", hour=10, minute=i)
                for i in range(40)])
    assert len(match_events(conn, "west", "Panel")) == 40


def test_match_narrows_by_day_and_exact_category(conn):
    seed(conn, [
        make_event(gt_id="fri", title="Jackbox", day=date(2026, 9, 4)),
        make_event(gt_id="sat", title="Jackbox", day=date(2026, 9, 5)),
        make_event(gt_id="sat-other", title="Jackbox", day=date(2026, 9, 5),
                   categories=("Tabletop",)),
    ])
    got = match_events(conn, "west", "jackbox", day=date(2026, 9, 5), category="panels")
    assert [e.gt_id for e in got] == ["sat"]


def test_match_excludes_cancelled_and_other_shows(conn):
    from paxbot.store.events import mark_cancelled
    seed(conn, [
        make_event(gt_id="1", title="Jackbox"),
        make_event(gt_id="2", title="Jackbox Gone"),
        make_event(gt_id="3", title="Jackbox", show_slug="unplugged"),
    ])
    with transaction(conn):
        mark_cancelled(conn, "west", ["2"])
    assert [e.gt_id for e in match_events(conn, "west", "jackbox")] == ["1"]
