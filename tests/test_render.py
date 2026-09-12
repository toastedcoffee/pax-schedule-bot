from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from paxbot.bot import ids, render
from paxbot.store.db import transaction
from paxbot.store.events import upsert_events
from paxbot.views import PanelFilters, panel_view, saved_view
from paxbot.store.saved import save_event
from tests.conftest import make_event
from tests.test_views import SHOW, THRESHOLD, USER, FRI

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)


def seed(conn, events):
    with transaction(conn):
        upsert_events(conn, events)


def state_for(conn, **kw):
    return panel_view(conn, SHOW, PanelFilters(day=FRI, **kw), USER, THRESHOLD, NOW)


def test_local_span_uses_show_time_not_utc():
    event = make_event(hour=18, minutes=90)      # 18:00 UTC == 11:00 Pacific
    assert render.local_span(event, TZ) == "11:00am–12:30pm"


def test_panel_embed_lists_the_page(conn):
    seed(conn, [make_event(gt_id="1", title="Indie Showcase", hour=17)])
    embed = render.panel_embed(state_for(conn))
    assert "Indie Showcase" in embed.fields[0].name


def test_panel_embed_names_drop_ins_rather_than_counting_them(conn):
    seed(conn, [
        make_event(gt_id="p", title="A Panel", hour=17, minutes=60),
        make_event(gt_id="d", title="Handheld Lounge", hour=17, minutes=600),
    ])
    embed = render.panel_embed(state_for(conn))
    assert "Handheld Lounge" in (embed.footer.text or "")


def test_panel_embed_has_no_footer_when_no_drop_ins(conn):
    seed(conn, [make_event(gt_id="p", hour=17, minutes=60)])
    assert not (render.panel_embed(state_for(conn)).footer.text or "")


def test_panel_embed_says_so_when_the_store_is_empty(conn):
    embed = render.panel_embed(state_for(conn))
    assert "no schedule loaded" in (embed.description or "").lower()


def test_panel_embed_stays_under_the_total_character_limit(conn):
    """Six 122-character titles with long locations must still fit in 6000."""
    seed(conn, [
        make_event(gt_id=str(i), hour=17, minute=i, title="T" * 122,
                   location="L" * 100)
        for i in range(12)
    ])
    embed = render.panel_embed(state_for(conn))
    assert len(embed) <= render.EMBED_TOTAL_MAX


def test_panel_embed_field_count_stays_under_the_limit(conn):
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i) for i in range(30)])
    assert len(render.panel_embed(state_for(conn)).fields) <= 25


def test_day_options_fit_the_select(conn):
    assert len(render.day_options(SHOW)) <= render.SELECT_OPTION_MAX


def test_hour_options_come_from_the_day_and_fit(conn):
    seed(conn, [make_event(gt_id=str(h), hour=h) for h in range(17, 24)])
    options = render.hour_options(state_for(conn))
    assert len(options) <= render.SELECT_OPTION_MAX
    # "All day" is the only non-numeric option and it MUST be present: without
    # it a user who picks an hour can never return to the whole-day view except
    # via Reset, which also clears their day and category.
    assert options[0].value == render.ALL_VALUE
    assert all(o.value.isdigit() for o in options[1:])


def test_category_options_never_exceed_the_select_limit(conn):
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i,
                           categories=(f"Cat{i:02}",)) for i in range(40)])
    assert len(render.category_options(state_for(conn))) <= render.SELECT_OPTION_MAX


def test_category_options_include_an_all_choice(conn):
    seed(conn, [make_event(gt_id="1", hour=17, categories=("Tabletop",))])
    assert render.category_options(state_for(conn))[0].value == render.ALL_VALUE


def test_autocomplete_label_is_truncated_to_discords_limit():
    event = make_event(title="T" * 200)
    assert len(render.autocomplete_label(event, TZ)) <= render.AUTOCOMPLETE_LABEL_MAX


def test_autocomplete_label_keeps_the_time_when_truncating():
    event = make_event(title="T" * 200, hour=18)
    assert "11:00am" in render.autocomplete_label(event, TZ)


def test_event_embed_marks_a_drop_in():
    event = make_event(hour=17, minutes=600)
    embed = render.event_embed(SHOW, event, THRESHOLD, saved=False)
    assert "drop-in" in (embed.description or "").lower()


def test_event_embed_stays_under_the_limit_with_a_long_description():
    event = make_event(title="T" * 122)
    event = type(event)(**{**event.__dict__, "description": "D" * 8000})
    assert len(render.event_embed(SHOW, event, THRESHOLD, saved=False)) <= render.EMBED_TOTAL_MAX


def test_saved_embed_flags_conflicts(conn):
    seed(conn, [make_event(gt_id="a", hour=17, minutes=120),
                make_event(gt_id="b", hour=18, minutes=60)])
    save_event(conn, USER, "west", "a")
    save_event(conn, USER, "west", "b")
    embed = render.saved_embed(saved_view(conn, SHOW, USER, THRESHOLD), THRESHOLD)
    assert "conflict" in str(embed.to_dict()).lower()


def test_saved_embed_reports_removed_events(conn):
    save_event(conn, USER, "west", "ghost")
    embed = render.saved_embed(saved_view(conn, SHOW, USER, THRESHOLD), THRESHOLD)
    assert "removed" in str(embed.to_dict()).lower()


def test_saved_embed_is_helpful_when_empty(conn):
    embed = render.saved_embed(saved_view(conn, SHOW, USER, THRESHOLD), THRESHOLD)
    assert "/schedule" in str(embed.to_dict())


def test_saved_embed_does_not_silently_drop_a_long_day(conn):
    """A keen attendee saves 16+ things on one day. One embed field caps at
    1024 characters, which truncated the list with only an ellipsis to show for
    it - hiding events from the one view whose whole job is showing them."""
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i % 60,
                           title=f"Event number {i} with a reasonably long name")
                for i in range(30)])
    for i in range(30):
        save_event(conn, USER, "west", str(i))
    embed = render.saved_embed(saved_view(conn, SHOW, USER, THRESHOLD), THRESHOLD)
    rendered = "\n".join(f.value for f in embed.fields)
    assert rendered.count("Event number") == 30
    assert len(embed) <= render.EMBED_TOTAL_MAX


def test_saved_embed_says_how_many_did_not_fit(conn):
    """Past what even chunking can hold, name the shortfall instead of eliding."""
    seed(conn, [make_event(gt_id=str(i), hour=17, minute=i % 60,
                           title=f"E{i} " + "x" * 90) for i in range(120)])
    for i in range(120):
        save_event(conn, USER, "west", str(i))
    embed = render.saved_embed(saved_view(conn, SHOW, USER, THRESHOLD), THRESHOLD)
    assert len(embed) <= render.EMBED_TOTAL_MAX
    assert "did not fit" in str(embed.to_dict())
