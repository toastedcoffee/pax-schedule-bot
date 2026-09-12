"""View models -> Discord payloads. Pure: no I/O, no interaction objects.

Everything Discord silently truncates or rejects is enforced here, where it is
a unit test rather than a production mystery.
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import discord

from paxbot.config import Show
from paxbot.models import Event
from paxbot.views import PanelState, SavedState

EMBED_TOTAL_MAX = 6000
# Headroom kept free so the "Not shown" and "Removed upstream" notices always
# fit, plus two reserved field slots for the same reason.
TOTAL_RESERVE = 400
MAX_SCHEDULE_FIELDS = 23
# The panel footer names drop-ins; capped far under Discord's 2048 so it
# cannot eat the budget the event fields need.
FOOTER_MAX = 400
# Room for the " (+NNN more)" suffix the footer appends when drop-in names
# do not all fit.
MORE_SUFFIX_MAX = 16
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_VALUE_MAX = 1024
SELECT_OPTION_MAX = 25
SELECT_LABEL_MAX = 100
AUTOCOMPLETE_LABEL_MAX = 100
FIELD_NAME_MAX = 256

ALL_VALUE = "*"          # the "no category filter" select value
ACCENT = discord.Colour(0x5865F2)


def _clip(text: str, limit: int) -> str:
    """Truncate with an ellipsis. Discord rejects over-length values outright.

    A non-positive limit returns empty rather than falling through to Python's
    negative slicing, which keeps almost the whole string - the exact opposite
    of clipping, and wrong precisely when the input is most extreme. Measured:
    _clip(8000 chars, limit=-304) returned 7696 characters.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    """Pack lines into blocks of at most `limit` characters, never splitting one.

    One line is one event, so splitting mid-line would render half an event.
    """
    blocks: list[str] = []
    current = ""
    for line in lines:
        # Clip first: a single line longer than the limit would otherwise be
        # placed unchecked into an empty block and emitted oversized.
        line = _clip(line, limit)
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            blocks.append(current)
            current = line
        else:
            current = candidate
    if current:
        blocks.append(current)
    return blocks


def _clock(moment) -> str:
    """'11:30am'. %-I is not portable to Windows, so strip the zero by hand."""
    return moment.strftime("%I:%M%p").lstrip("0").lower()


def local_span(event: Event, tz: ZoneInfo) -> str:
    """'11:30am–1:00pm', always in the show's timezone, never the viewer's."""
    start = event.starts_at.astimezone(tz)
    end = event.ends_at.astimezone(tz)
    return f"{_clock(start)}–{_clock(end)}"


def _tz(show: Show) -> ZoneInfo:
    return ZoneInfo(show.timezone)


def panel_embed(state: PanelState) -> discord.Embed:
    tz = _tz(state.show)
    title = f"{state.show.name} — {state.filters.day:%A, %B %d}"

    if state.total == 0 and not state.drop_ins:
        # Distinguish "nothing matches your filters" from "nothing is loaded":
        # a user who has never synced should be told that, not told Saturday
        # is empty.
        body = ("No schedule loaded yet — run a sync first."
                if _store_looks_empty(state)
                else "Nothing matches these filters.")
        return discord.Embed(title=title, description=body, colour=ACCENT)

    bits = []
    if state.filters.hour is not None:
        hour = state.filters.hour
        bits.append(f"Running at **{_hour_label(hour)}**")
    else:
        bits.append("**All day**")
    bits.append(state.filters.category or "all categories")
    bits.append(f"**{state.total}** events")
    if state.page_count > 1:
        bits.append(f"page {state.page + 1} of {state.page_count}")

    embed = discord.Embed(title=title, description=" · ".join(bits),
                          colour=ACCENT)
    # Discord counts the WHOLE embed against 6000 and reports nothing when it
    # overflows. Per-field clips alone cannot hold that line: six events at
    # FIELD_NAME_MAX + EMBED_FIELD_VALUE_MAX is already 7680. PAGE_SIZE lives in
    # views.py, so rather than hardcode arithmetic that a change there would
    # silently invalidate, share the remaining budget across however many events
    # this page actually holds.
    spare = EMBED_TOTAL_MAX - TOTAL_RESERVE - len(embed) - FOOTER_MAX
    per_event = max(1, spare // max(1, len(state.events)))
    # Floor of 1: Discord requires a non-empty field name, so a vanishing
    # budget must still yield one character rather than an empty string.
    name_max = max(1, min(FIELD_NAME_MAX, per_event // 2))
    value_max = min(EMBED_FIELD_VALUE_MAX, per_event - name_max)
    for index, event in enumerate(state.events, start=1):
        star = "⭐ " if event.gt_id in state.saved_ids else ""
        cancelled = " — **CANCELLED**" if event.cancelled else ""
        embed.add_field(
            name=_clip(f"{index}. {star}{event.title}", name_max),
            value=_clip(f"{local_span(event, tz)} · {event.location}{cancelled}",
                        value_max),
            inline=False,
        )

    if state.drop_ins:
        # Named, not counted: "6 drop-ins open now" tells you nothing you can
        # act on. Text only - all five action rows are already spent.
        #
        # But name as many as fit and then SAY how many did not, rather than
        # ending on a bare ellipsis. This is reachable on real data, not
        # theoretical: Saturday 2pm on the PAX West 2026 schedule has 23
        # drop-ins running, a 907-character list against a 400-character cap.
        prefix = "＋ Open now: "
        budget = FOOTER_MAX - len(prefix) - MORE_SUFFIX_MAX
        shown: list[str] = []
        used = 0
        for event in state.drop_ins:
            cost = len(event.title) + (3 if shown else 0)
            if used + cost > budget:
                break
            shown.append(event.title)
            used += cost
        text = prefix + " · ".join(shown)
        hidden = len(state.drop_ins) - len(shown)
        if hidden:
            text += f" (+{hidden} more)"
        embed.set_footer(text=_clip(text, FOOTER_MAX))
    return embed


def _store_looks_empty(state: PanelState) -> bool:
    return not state.hours and not state.categories


def _hour_label(hour: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}:00{suffix}"


def day_options(show: Show) -> list[discord.SelectOption]:
    options = []
    current = show.start_date
    while current <= show.end_date:
        options.append(discord.SelectOption(
            label=_clip(f"{current:%A, %b %d}", SELECT_LABEL_MAX),
            value=current.isoformat(),
        ))
        current = date.fromordinal(current.toordinal() + 1)
    return options[:SELECT_OPTION_MAX]


def hour_options(state: PanelState) -> list[discord.SelectOption]:
    """Built from the day's own hours: days do not share opening times.

    The currently selected hour is force-included even when nothing is running
    in it. default_filters() opens the panel on the current wall-clock hour,
    which outside show hours is not among state.hours - and a select whose
    selected value is missing from its own options renders blank.
    """
    hours = set(state.hours)
    if state.filters.hour is not None:
        hours.add(state.filters.hour)
    options = [discord.SelectOption(label="All day", value=ALL_VALUE)]
    options += [discord.SelectOption(label=_hour_label(hour), value=str(hour))
                for hour in sorted(hours)]
    return options[:SELECT_OPTION_MAX]


def category_options(state: PanelState) -> list[discord.SelectOption]:
    options = [discord.SelectOption(label="All categories", value=ALL_VALUE)]
    for name, count in state.categories:
        options.append(discord.SelectOption(
            label=_clip(f"{name} ({count})", SELECT_LABEL_MAX), value=name))
    return options[:SELECT_OPTION_MAX]


def autocomplete_label(event: Event, tz: ZoneInfo) -> str:
    """'Fri 11:00am — Title'. The time is prefixed so truncation never eats it."""
    prefix = f"{event.day:%a} {_clock(event.starts_at.astimezone(tz))} — "
    return prefix + _clip(event.title, AUTOCOMPLETE_LABEL_MAX - len(prefix))


def event_embed(show: Show, event: Event, threshold_minutes: int,
                saved: bool) -> discord.Embed:
    tz = _tz(show)
    span = local_span(event, tz)
    marker = " · all-day drop-in" if event.is_drop_in(threshold_minutes) else ""
    # The location is clipped INTO the header: unbounded upstream text there
    # would otherwise drive the description budget below zero.
    header = f"{event.day:%A} {span}{marker}\n{_clip(event.location, 200)}"
    body = _clip(event.description,
                 max(0, EMBED_DESCRIPTION_MAX - len(header) - 200))
    embed = discord.Embed(
        title=_clip(("⭐ " if saved else "") + event.title, FIELD_NAME_MAX),
        description=f"{header}\n\n{body}",
        colour=ACCENT,
        url=event.url or None,
    )
    if event.categories:
        embed.add_field(name="Categories",
                        value=_clip(", ".join(event.categories),
                                    EMBED_FIELD_VALUE_MAX),
                        inline=False)
    return embed


def saved_embed(state: SavedState, threshold_minutes: int) -> discord.Embed:
    tz = _tz(state.show)
    if state.total == 0 and not state.missing:
        return discord.Embed(
            title=f"{state.show.name} — your schedule",
            description="Nothing saved yet. Use `/schedule` or `/find` to add events.",
            colour=ACCENT,
        )

    embed = discord.Embed(
        title=f"{state.show.name} — your schedule",
        description=f"**{state.total}** saved",
        colour=ACCENT,
    )
    blocks: list[tuple[str, str]] = []
    for saved_day in state.days:
        lines = []
        for event in saved_day.events:
            flag = " ⚠️ conflict" if event.gt_id in saved_day.conflict_ids else ""
            drop = (" · drop-in"
                    if event.is_drop_in(threshold_minutes) else "")
            # Cancelled events stay listed and marked. Dropping them would
            # remove something from a plan with no explanation, which is the
            # exact failure this bot exists to fix.
            dead = " — **CANCELLED**" if event.cancelled else ""
            lines.append(
                f"{local_span(event, tz)} · {event.title}{drop}{flag}{dead}")
        # Chunk rather than clip. A single field caps at 1024 characters, which
        # silently swallowed events past roughly the fifteenth on a busy day -
        # in the one view whose whole job is showing someone their schedule.
        for index, block in enumerate(_chunk_lines(lines, EMBED_FIELD_VALUE_MAX)):
            suffix = "" if index == 0 else " (continued)"
            blocks.append((f"{saved_day.day:%A, %B %d}{suffix}", block))

    used = len(embed)
    hidden = 0
    for name, value in blocks:
        over = used + len(name) + len(value) > EMBED_TOTAL_MAX - TOTAL_RESERVE
        if over or len(embed.fields) >= MAX_SCHEDULE_FIELDS:
            hidden += value.count("\n") + 1
            continue
        embed.add_field(name=name, value=value, inline=False)
        used += len(name) + len(value)

    if hidden:
        # Say so rather than ending on a bare ellipsis. Narrowing to one day
        # frees the budget the other days were consuming.
        embed.add_field(
            name="Not shown",
            value=f"{hidden} more saved event(s) did not fit. "
                  "Use `/me day:YYYY-MM-DD` to narrow to one day.",
            inline=False,
        )

    if state.missing:
        embed.add_field(
            name="Removed upstream",
            value=_clip(
                f"{len(state.missing)} saved event(s) are no longer in the "
                "schedule. They are kept here rather than silently dropped.",
                EMBED_FIELD_VALUE_MAX),
            inline=False,
        )
    return embed
