from datetime import UTC, date, datetime

import pytest

from paxbot.config import Show
from paxbot.store.db import transaction
from paxbot.store.events import mark_cancelled, upsert_events
from paxbot.store.saved import save_event
from paxbot.views import PAGE_SIZE, PanelFilters, default_filters, panel_view
from tests.conftest import make_event

SHOW = Show(
    slug="west", name="PAX West 2026", base_url="https://west.paxsite.com",
    api_key="k", timezone="America/Los_Angeles",
    start_date=date(2026, 9, 4), end_date=date(2026, 9, 7),
)
USER = "123456789012345678"
THRESHOLD = 240
FRI = date(2026, 9, 4)

# Pacific is UTC-7 in September, so 18:00 UTC is 11:00 local.
def seed(conn, events):
    with transaction(conn):
        upsert_events(conn, events)


def filters(hour=None, category=None, page=0, day=FRI):
    return PanelFilters(day=day, hour=hour, category=category, page=page)


def test_whole_day_returns_every_event(conn):
    seed(conn, [make_event(gt_id=str(h), hour=h) for h in (17, 18, 19)])
    state = panel_view(conn, SHOW, filters(), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.total == 3


def test_hour_filter_uses_overlap_not_start(conn):
    """An event starting at 10:00 local and running 3h is still 'running at' 12:00."""
    seed(conn, [
        make_event(gt_id="long", hour=17, minutes=180),   # 10:00-13:00 local
        make_event(gt_id="other", hour=23, minutes=60),   # 16:00-17:00 local
    ])
    state = panel_view(conn, SHOW, filters(hour=12), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["long"]


def test_event_ending_exactly_on_the_hour_is_excluded(conn):
    """Half-open interval: [start, end). An event ending at 12:00 is not
    running at 12:00, or every boundary would double-count."""
    seed(conn, [make_event(gt_id="ends", hour=18, minutes=60)])  # 11:00-12:00 local
    state = panel_view(conn, SHOW, filters(hour=12), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.events == ()


def test_event_starting_exactly_on_the_hour_is_included(conn):
    seed(conn, [make_event(gt_id="starts", hour=19, minutes=60)])  # 12:00-13:00 local
    state = panel_view(conn, SHOW, filters(hour=12), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["starts"]


def test_drop_ins_are_split_out_of_the_paged_list(conn):
    seed(conn, [
        make_event(gt_id="panel", hour=19, minutes=60),
        make_event(gt_id="lounge", hour=17, minutes=600),   # 10h, a drop-in
    ])
    state = panel_view(conn, SHOW, filters(hour=12), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["panel"]
    assert [e.gt_id for e in state.drop_ins] == ["lounge"]
    assert state.total == 1


def test_drop_in_threshold_is_configurable(conn):
    seed(conn, [make_event(gt_id="x", hour=17, minutes=300)])  # 5h
    now = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    assert panel_view(conn, SHOW, filters(), USER, 240, now).drop_ins != ()
    assert panel_view(conn, SHOW, filters(), USER, 360, now).drop_ins == ()


def test_pagination_splits_and_reports_page_count(conn):
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i) for i in range(14)])
    now = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    first = panel_view(conn, SHOW, filters(page=0), USER, THRESHOLD, now)
    assert len(first.events) == PAGE_SIZE
    assert first.page_count == 3
    last = panel_view(conn, SHOW, filters(page=2), USER, THRESHOLD, now)
    assert len(last.events) == 14 - 2 * PAGE_SIZE


def test_page_size_fits_one_discord_action_row():
    """One star button per event, all sharing a single action row - and Discord
    caps a row at 5 buttons.

    Asserted on the constant because the panel that would break lives in
    paxbot/bot/panel.py, which has no unit tests by design. Without this, a
    well-meaning bump to PAGE_SIZE would surface only as a runtime
    "item would not fit at row 4" the first time a full page rendered.
    """
    assert PAGE_SIZE <= 5


def test_out_of_range_page_is_clamped(conn):
    """A stale panel click must not produce an empty page with a live Next button."""
    seed(conn, [make_event(gt_id="1", hour=17)])
    state = panel_view(conn, SHOW, filters(page=99), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.page == 0
    assert len(state.events) == 1


def test_category_filter(conn):
    seed(conn, [
        make_event(gt_id="t", hour=17, categories=("Tabletop",)),
        make_event(gt_id="p", hour=17, categories=("Panels",)),
    ])
    state = panel_view(conn, SHOW, filters(category="Panels"), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["p"]


def test_hours_come_from_the_day_not_a_fixed_range(conn):
    """Sunday runs shorter than Saturday; a hardcoded range would offer
    empty hours."""
    seed(conn, [make_event(gt_id="a", hour=17), make_event(gt_id="b", hour=22)])
    state = panel_view(conn, SHOW, filters(), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.hours == (10, 15)


def test_hours_include_an_hour_where_nothing_starts(conn):
    """The panel filters by overlap, so an hour with a running event must be
    selectable even when nothing starts in it. On real PAX West data nothing
    starts at 11pm on Friday, yet events run until midnight - deriving hours
    from start times alone makes that hour unreachable."""
    seed(conn, [make_event(gt_id="long", hour=17, minutes=180)])  # 10:00-13:00
    state = panel_view(conn, SHOW, filters(), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.hours == (10, 11, 12)


def test_hours_are_not_narrowed_by_the_category_filter(conn):
    """Sibling facets must not shrink each other's options, or the hour list
    changes under the user when they pick a category."""
    seed(conn, [
        make_event(gt_id="t", hour=17, categories=("Tabletop",)),   # 10:00
        make_event(gt_id="p", hour=22, categories=("Panels",)),     # 15:00
    ])
    state = panel_view(conn, SHOW, filters(category="Panels"), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["p"]
    assert state.hours == (10, 15)


def test_categories_are_capped_at_the_select_limit(conn):
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i,
                           categories=(f"Cat{i:02}",)) for i in range(30)])
    state = panel_view(conn, SHOW, filters(), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert len(state.categories) == 24


def test_saved_ids_are_reported_for_star_rendering(conn):
    seed(conn, [make_event(gt_id="1", hour=17), make_event(gt_id="2", hour=17)])
    save_event(conn, USER, "west", "1")
    state = panel_view(conn, SHOW, filters(), USER, THRESHOLD,
                       datetime(2026, 9, 4, 18, 0, tzinfo=UTC))
    assert state.saved_ids == frozenset({"1"})


def test_default_filters_during_the_show_use_today_and_now(conn):
    seed(conn, [make_event(gt_id="1", hour=21, day=date(2026, 9, 5))])
    now = datetime(2026, 9, 5, 21, 30, tzinfo=UTC)   # 14:30 local, Saturday
    f = default_filters(conn, SHOW, now)
    assert f.day == date(2026, 9, 5)
    assert f.hour == 14


def test_default_filters_before_the_show_use_day_one_and_no_hour(conn):
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    f = default_filters(conn, SHOW, now)
    assert f.day == date(2026, 9, 4)
    assert f.hour is None


from paxbot.views import find_conflicts, saved_view


def test_no_conflict_when_times_do_not_overlap(conn):
    seed(conn, [make_event(gt_id="a", hour=17, minutes=60),
                make_event(gt_id="b", hour=19, minutes=60)])
    save_event(conn, USER, "west", "a")
    from paxbot.store.events import get_event
    new = get_event(conn, "west", "b")
    assert find_conflicts(conn, SHOW, USER, new, THRESHOLD) == ()


def test_conflict_is_detected_and_names_the_event(conn):
    seed(conn, [make_event(gt_id="a", title="Keynote", hour=17, minutes=120),
                make_event(gt_id="b", hour=18, minutes=60)])
    save_event(conn, USER, "west", "a")
    from paxbot.store.events import get_event
    got = find_conflicts(conn, SHOW, USER, get_event(conn, "west", "b"), THRESHOLD)
    assert [e.title for e in got] == ["Keynote"]


def test_touching_events_do_not_conflict(conn):
    """A 10:00-11:00 and an 11:00-12:00 are back to back, not overlapping."""
    seed(conn, [make_event(gt_id="a", hour=17, minutes=60),
                make_event(gt_id="b", hour=18, minutes=60)])
    save_event(conn, USER, "west", "a")
    from paxbot.store.events import get_event
    assert find_conflicts(conn, SHOW, USER, get_event(conn, "west", "b"),
                          THRESHOLD) == ()


def test_a_drop_in_never_raises_a_conflict(conn):
    seed(conn, [make_event(gt_id="lounge", hour=17, minutes=600),
                make_event(gt_id="panel", hour=18, minutes=60)])
    save_event(conn, USER, "west", "lounge")
    from paxbot.store.events import get_event
    assert find_conflicts(conn, SHOW, USER, get_event(conn, "west", "panel"),
                          THRESHOLD) == ()


def test_saving_a_drop_in_never_reports_conflicts(conn):
    seed(conn, [make_event(gt_id="panel", hour=17, minutes=60),
                make_event(gt_id="lounge", hour=17, minutes=600)])
    save_event(conn, USER, "west", "panel")
    from paxbot.store.events import get_event
    assert find_conflicts(conn, SHOW, USER, get_event(conn, "west", "lounge"),
                          THRESHOLD) == ()


def test_an_event_does_not_conflict_with_itself(conn):
    seed(conn, [make_event(gt_id="a", hour=17, minutes=60)])
    save_event(conn, USER, "west", "a")
    from paxbot.store.events import get_event
    assert find_conflicts(conn, SHOW, USER, get_event(conn, "west", "a"),
                          THRESHOLD) == ()


def test_saved_view_groups_by_day_and_flags_conflicts(conn):
    seed(conn, [
        make_event(gt_id="a", hour=17, minutes=120, day=FRI),
        make_event(gt_id="b", hour=18, minutes=60, day=FRI),
        make_event(gt_id="c", hour=17, minutes=60, day=date(2026, 9, 5)),
    ])
    for gt in ("a", "b", "c"):
        save_event(conn, USER, "west", gt)
    state = saved_view(conn, SHOW, USER, THRESHOLD)
    assert [d.day for d in state.days] == [FRI, date(2026, 9, 5)]
    assert state.days[0].conflict_ids == frozenset({"a", "b"})
    assert state.days[1].conflict_ids == frozenset()
    assert state.total == 3


def test_saved_view_filters_to_one_day(conn):
    seed(conn, [make_event(gt_id="a", day=FRI),
                make_event(gt_id="c", day=date(2026, 9, 5))])
    save_event(conn, USER, "west", "a")
    save_event(conn, USER, "west", "c")
    state = saved_view(conn, SHOW, USER, THRESHOLD, day=FRI)
    assert [d.day for d in state.days] == [FRI]


def test_saved_view_reports_removed_events(conn):
    save_event(conn, USER, "west", "ghost")
    state = saved_view(conn, SHOW, USER, THRESHOLD)
    assert state.missing == ("ghost",)


def test_a_cancelled_saved_event_does_not_raise_a_conflict(conn):
    """A cancelled event is not happening, so the slot it held is free."""
    seed(conn, [make_event(gt_id="dead", title="Cancelled Panel",
                           hour=17, minutes=120),
                make_event(gt_id="new", hour=18, minutes=60)])
    with transaction(conn):
        mark_cancelled(conn, "west", ["dead"])
    save_event(conn, USER, "west", "dead")
    from paxbot.store.events import get_event
    assert find_conflicts(conn, SHOW, USER,
                          get_event(conn, "west", "new"), THRESHOLD) == ()


def test_saved_view_does_not_flag_conflicts_against_cancelled_events(conn):
    seed(conn, [make_event(gt_id="dead", hour=17, minutes=120),
                make_event(gt_id="live", hour=18, minutes=60)])
    with transaction(conn):
        mark_cancelled(conn, "west", ["dead"])
    save_event(conn, USER, "west", "dead")
    save_event(conn, USER, "west", "live")
    state = saved_view(conn, SHOW, USER, THRESHOLD)
    assert [e.gt_id for e in state.days[0].events] == ["dead", "live"]
    assert state.days[0].conflict_ids == frozenset()


def test_saved_view_flags_all_pairs_not_just_adjacent_ones(conn):
    """Hub-and-spokes: two events that each overlap a third but not each other.

    Sorted by start time the pairs are (hub, spoke1), (hub, spoke2),
    (spoke1, spoke2). An adjacent-only comparison would catch the first,
    miss the second, and leave spoke2 unflagged - a silently missed conflict.
    """
    seed(conn, [
        make_event(gt_id="hub", hour=17, minutes=180),              # 10:00-13:00
        make_event(gt_id="spoke1", hour=17, minute=30, minutes=30),  # 10:30-11:00
        make_event(gt_id="spoke2", hour=19, minutes=30),             # 12:00-12:30
    ])
    for gt in ("hub", "spoke1", "spoke2"):
        save_event(conn, USER, "west", gt)
    state = saved_view(conn, SHOW, USER, THRESHOLD)
    assert state.days[0].conflict_ids == frozenset({"hub", "spoke1", "spoke2"})


def test_find_conflicts_returns_earliest_first(conn):
    """The caller names clashes[0] and counts the rest, so order is load-bearing."""
    seed(conn, [
        make_event(gt_id="late", hour=18, minutes=60),               # 11:00-12:00
        make_event(gt_id="early", hour=17, minutes=120),             # 10:00-12:00
        make_event(gt_id="new", hour=17, minute=45, minutes=30),     # 10:45-11:15
    ])
    save_event(conn, USER, "west", "late")
    save_event(conn, USER, "west", "early")
    from paxbot.store.events import get_event
    got = find_conflicts(conn, SHOW, USER, get_event(conn, "west", "new"), THRESHOLD)
    assert [e.gt_id for e in got] == ["early", "late"]


# ---- search mode ----------------------------------------------------------
SAT = date(2026, 9, 5)


def test_search_spans_every_day_when_no_day_is_chosen(conn):
    seed(conn, [
        make_event(gt_id="sat", title="Jackbox Party", day=SAT),
        make_event(gt_id="fri", title="Jackbox Party", day=FRI),
        make_event(gt_id="miss", title="Omegathon", day=FRI),
    ])
    state = panel_view(conn, SHOW, PanelFilters(day=None, query="jackbox"),
                       USER, THRESHOLD, datetime(2026, 9, 1, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["fri", "sat"]
    assert state.total == 2
    # No day means no hour options: an hour is only meaningful within a day.
    assert state.hours == ()


def test_search_pages_drop_ins_instead_of_collapsing_them(conn):
    """The footer collapse exists because drop-ins crowd a time window. A search
    names them explicitly, and a footer entry has no star button, so collapsing
    would leave a searched-for drop-in unstarrable from the panel."""
    seed(conn, [make_event(gt_id="drop", title="Crokinole Freeplay", minutes=480)])
    state = panel_view(conn, SHOW, PanelFilters(day=None, query="crokinole"),
                       USER, THRESHOLD, datetime(2026, 9, 1, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["drop"]
    assert state.drop_ins == ()


def test_search_narrows_by_day_hour_and_category(conn):
    seed(conn, [
        make_event(gt_id="hit", title="Jackbox", hour=19),     # 12:00 local Fri
        make_event(gt_id="wrong-hour", title="Jackbox", hour=23),
        make_event(gt_id="wrong-day", title="Jackbox", hour=19, day=SAT),
        make_event(gt_id="wrong-cat", title="Jackbox", hour=19, categories=("Tabletop",)),
    ])
    state = panel_view(conn, SHOW,
                       PanelFilters(day=FRI, hour=12, category="Panels", query="jackbox"),
                       USER, THRESHOLD, datetime(2026, 9, 1, tzinfo=UTC))
    assert [e.gt_id for e in state.events] == ["hit"]


def test_a_filter_without_a_day_must_be_a_search():
    """All-days only exists inside a search. A browse panel spanning 713 events
    would be unusable, so the invariant is enforced rather than assumed."""
    with pytest.raises(ValueError):
        PanelFilters(day=None)
