from datetime import UTC, date, datetime

import pytest

from paxbot.config import Show
from paxbot.store.db import transaction
from paxbot.store.events import upsert_events
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
    assert len(last.events) == 2


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
