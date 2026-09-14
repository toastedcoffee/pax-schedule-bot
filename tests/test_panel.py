"""Regression tests for the /schedule panel's lifecycle, against discord.py's
real ViewStore.

paxbot/bot/ has no behavioural tests by project decision (spec section 10),
because mocked-interaction tests tend to assert discord.py's API shape rather
than this project's behaviour. These are a deliberate, narrow exception, added
for a production bug: on a panel older than 15 minutes, pressing "Add anyway"
refreshed the panel through the /schedule command's expired token. The edit
failed after rebuild() had already detached the old buttons, leaving
discord.py's ViewStore pointing at detached items, so every later click was
discarded with "View interaction referencing unknown view".

The Search tests at the bottom were added under the same exception, for the
owner's smoke-test report that Search posted a separate reply and never touched
the panel. They pin the same invariants: Search answers by editing the panel in
place, becomes its anchor, and leaves the store attached.

The invariants pinned here are this project's own - out-of-band panel edits go
through the newest click's token, and a failed edit never leaves the store
referencing detached items. The only fakes are the interaction methods the
panel actually calls, and on success they register the view exactly as
discord.py does.
"""
import asyncio
import types
from datetime import UTC, datetime

import discord
import pytest
from discord.ui.view import ViewStore

from paxbot.bot import ids
from paxbot.bot.panel import ConfirmSave, SchedulePanel, SearchModal
from paxbot.store.db import transaction
from paxbot.store.events import upsert_events
from paxbot.store.saved import save_event
from paxbot.views import PanelFilters
from tests.conftest import make_event
from tests.test_views import FRI, SAT, SHOW, THRESHOLD, USER

MESSAGE_ID = 1
RESET_ID = ids.encode(ids.RESET)
# Before the show, so the panel renders no "Now" button.
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _token_expired():
    return discord.HTTPException(
        types.SimpleNamespace(status=401, reason="Unauthorized"),
        "Invalid Webhook Token")


async def _noop(**kwargs):
    return None


class _Response:
    def __init__(self, click):
        self._click = click
        self.edits = []
        self.sent = []

    def is_done(self):
        return bool(self.edits or self.sent)

    async def edit_message(self, **kwargs):
        if self._click.dead:
            raise _token_expired()
        self.edits.append(kwargs)
        self._click.register(kwargs.get("view"))

    async def send_message(self, *args, **kwargs):
        if self._click.dead:
            raise _token_expired()
        self.sent.append((args, kwargs))


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return types.SimpleNamespace(edit=_noop)


class _Click:
    """A component interaction. `dead` models a token past its 15 minutes."""

    def __init__(self, store=None, custom_id="", dead=False):
        self.store = store
        self.dead = dead
        self.data = {"custom_id": custom_id}
        self.user = types.SimpleNamespace(id=int(USER))
        self.response = _Response(self)
        self.followup = _Followup()
        self.original_edits = []

    def register(self, view):
        # discord.py re-registers the view in its store after a successful edit.
        if self.store is not None and view is not None:
            self.store.add_view(view, MESSAGE_ID)

    async def edit_original_response(self, **kwargs):
        if self.dead:
            raise _token_expired()
        self.original_edits.append(kwargs)
        self.register(kwargs.get("view"))

    async def original_response(self):
        return types.SimpleNamespace(edit=_noop)


class _DeadMessage:
    """The /schedule command's response message, after its token expired."""

    async def edit(self, **kwargs):
        raise _token_expired()


def _seed_three_pages(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id=str(i), hour=17, minute=i)
                             for i in range(12)])


def _panel(conn, store_holder, page=0):
    panel = SchedulePanel(conn=conn, show=SHOW, user_id=int(USER),
                          filters=PanelFilters(day=FRI, page=page),
                          threshold_minutes=THRESHOLD, now_factory=lambda: NOW)
    store = ViewStore(state=None)
    store.add_view(panel, MESSAGE_ID)      # what the initial send does
    store_holder.append(store)
    return panel


def _expire_command_token(panel):
    """Mark the /schedule command's handle dead under both the old attribute
    name and the new one, so each test pins behaviour across the fix."""
    panel.message = _DeadMessage()
    panel.anchor = _Click(dead=True)


def _assert_store_attached(store, panel):
    detached = [key for key, item in store._views[MESSAGE_ID].items()
                if item.view is not panel]
    assert detached == [], f"store references detached items: {detached}"


def test_add_anyway_on_an_old_panel_does_not_brick_it(conn):
    """The production bug, reproduced through its real entry point."""
    _seed_three_pages(conn)

    async def run():
        holder = []
        panel = _panel(conn, holder)
        _expire_command_token(panel)
        confirm = ConfirmSave(conn, SHOW, int(USER), "3", panel=panel)
        await confirm._confirm(_Click(holder[0]))
        _assert_store_attached(holder[0], panel)

    asyncio.run(run())


def test_a_failed_page_change_rolls_back_and_leaves_the_panel_usable(conn):
    """A failed in-band edit must restore the filters, the state and the
    buttons, or the panel's next click reads a page Discord never showed."""
    _seed_three_pages(conn)

    async def run():
        holder = []
        panel = _panel(conn, holder, page=0)
        with pytest.raises(discord.HTTPException):
            await panel._on_nav(_Click(holder[0], custom_id="px:n:", dead=True))
        assert panel.filters.page == 0
        assert panel.state.page == 0
        _assert_store_attached(holder[0], panel)

    asyncio.run(run())


def test_add_anyway_refreshes_the_panel_through_its_most_recent_click(conn):
    """The command token dies 15 minutes after /schedule, but a panel in use
    lives until 14 minutes after its last click."""
    _seed_three_pages(conn)

    async def run():
        holder = []
        panel = _panel(conn, holder)
        _expire_command_token(panel)
        latest = _Click(holder[0], custom_id="px:n:")
        await panel._on_nav(latest)
        confirm = ConfirmSave(conn, SHOW, int(USER), "3", panel=panel)
        await confirm._confirm(_Click(holder[0]))
        assert latest.original_edits, "panel not refreshed through the latest click"
        _assert_store_attached(holder[0], panel)

    asyncio.run(run())


def test_the_expiry_notice_goes_through_the_most_recent_click(conn):
    """The notice fires 14 minutes after the last click, which is inside that
    click's 15-minute token - and outside the command's, for any panel used
    past its first minute."""
    _seed_three_pages(conn)

    async def run():
        holder = []
        panel = _panel(conn, holder)
        _expire_command_token(panel)
        latest = _Click(holder[0], custom_id="px:n:")
        await panel._on_nav(latest)
        await panel.on_timeout()
        assert latest.original_edits, "expiry notice not sent through the latest click"
        assert "expired" in latest.original_edits[-1]["content"]
        assert all(child.disabled for child in panel.children)

    asyncio.run(run())


def test_a_conflicting_star_answers_in_place_and_prompts_in_a_followup(conn):
    """Answering the star click by editing the panel keeps that click usable as
    the refresh token for "Add anyway". Answering it with a new message would
    make the click's original response the prompt, not the panel."""
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="booked", hour=17, minutes=120),
                             make_event(gt_id="clash", hour=18, minutes=60)])
    save_event(conn, USER, "west", "booked")

    async def run():
        holder = []
        panel = _panel(conn, holder)
        click = _Click(holder[0], custom_id="px:s:clash")
        await panel._on_star(click)
        assert click.response.edits, "star click was not answered in place"
        assert not click.response.sent
        assert len(click.followup.sent) == 1
        _, kwargs = click.followup.sent[0]
        assert isinstance(kwargs["view"], ConfirmSave)
        assert kwargs.get("ephemeral") is True

    asyncio.run(run())



def _submit_search(panel, store, text):
    modal = SearchModal(panel)
    modal.query._value = text
    return modal, _Click(store)


def test_search_filters_the_panel_in_place(conn):
    """Owner smoke test, 2026-09-13: Search posted a separate text list with no
    star buttons and left the panel untouched."""
    with transaction(conn):
        upsert_events(conn, [
            make_event(gt_id="sat", title="Jackbox Party", day=SAT),
            make_event(gt_id="other", title="Omegathon"),
        ])

    async def run():
        holder = []
        panel = _panel(conn, holder)
        modal, click = _submit_search(panel, holder[0], "jackbox")
        await modal.on_submit(click)
        assert click.response.edits, "search did not edit the panel"
        assert not click.response.sent, "search posted a separate message"
        assert [e.gt_id for e in panel.state.events] == ["sat"]
        # The submit's response IS the panel, so it is the newest anchor.
        assert panel.anchor is click
        _assert_store_attached(holder[0], panel)

    asyncio.run(run())


def test_other_filters_keep_the_search_and_reset_clears_it(conn):
    with transaction(conn):
        upsert_events(conn, [make_event(gt_id="sat", title="Jackbox Party", day=SAT)])

    async def run():
        holder = []
        panel = _panel(conn, holder)
        modal, click = _submit_search(panel, holder[0], "jackbox")
        await modal.on_submit(click)

        pick = _Click(holder[0])
        pick.data["values"] = ["Panels"]
        await panel._on_category(pick)
        assert panel.filters.query == "jackbox"
        assert panel.filters.day is None

        await panel._on_nav(_Click(holder[0], custom_id=RESET_ID))
        assert panel.filters.query is None
        assert panel.filters.day == SHOW.start_date

    asyncio.run(run())


def test_a_blank_search_is_refused_without_touching_the_panel(conn):
    """min_length counts spaces, so a query of only spaces reaches on_submit."""
    _seed_three_pages(conn)

    async def run():
        holder = []
        panel = _panel(conn, holder)
        modal, click = _submit_search(panel, holder[0], "   ")
        await modal.on_submit(click)
        assert not click.response.edits
        assert click.response.sent
        assert panel.filters.query is None

    asyncio.run(run())
