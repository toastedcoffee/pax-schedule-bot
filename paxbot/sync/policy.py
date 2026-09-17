"""Which shows this tick should fetch. A pure decision, no I/O.

Separate from runner.py so the policy can be tested without a network call,
and separate from views.py because this answers a question the sync loop asks,
not one the bot's rendering asks.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from paxbot.config import Show


def shows_to_sync(
    shows: Sequence[Show],
    last_ok: Mapping[str, datetime | None],
    now: datetime,
    in_show_hours: int,
    off_season_hours: int,
) -> tuple[Show, ...]:
    """The shows due for a fetch right now, in the order given.

    A show is synced when it is ELIGIBLE and DUE.

    Eligible: never successfully synced, or it has not finished yet (judged in
    the show's own timezone). The never-synced clause is load-bearing - without
    it a show added to shows.toml after it ended would never be polled, so
    would never have data, so would be permanently invisible, and a fresh
    install made after a show ends would hold no data at all.

    Due: never successfully synced, or enough time has passed - one hour while
    that show is running, four hours otherwise, honouring LEAP's max-age=14400.

    `last_ok` values must be timezone-aware; a slug absent from the mapping and
    a slug mapped to None both mean "never successfully synced", so a newly
    added show never raises KeyError here.
    """
    if now.tzinfo is None:
        raise ValueError("shows_to_sync() needs a timezone-aware datetime")

    due: list[Show] = []
    for show in shows:
        last = last_ok.get(show.slug)
        if last is None:
            due.append(show)
            continue
        today = now.astimezone(ZoneInfo(show.timezone)).date()
        if today > show.end_date:
            continue
        wanted = in_show_hours if show.is_running(now) else off_season_hours
        if now - last >= timedelta(hours=wanted):
            due.append(show)
    return tuple(due)
