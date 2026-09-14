"""Show configuration. Adding a PAX is a block in shows.toml, not code."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DROP_IN_THRESHOLD_MINUTES = 240

# Literal names rather than strftime("%A") or calendar.day_name, both of which
# follow the process locale: matching what someone typed must not change with
# the machine the bot happens to run on. Indexed by date.weekday().
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday")
# Fewer letters cannot tell Saturday from Sunday, or Tuesday from Thursday.
_MIN_DAY_LETTERS = 3


class DayError(ValueError):
    """What someone typed does not name exactly one day of the show.

    The message is written for the person who typed it and is safe to send to
    Discord as-is.
    """


@dataclass(frozen=True)
class Show:
    slug: str
    name: str
    base_url: str
    api_key: str
    timezone: str
    start_date: date
    end_date: date

    def is_running(self, now: datetime) -> bool:
        """True when `now` falls on a show day, in the show's own timezone.

        The conversion matters: 2026-09-04 06:00 UTC is still 2026-09-03 in
        Seattle, and reporting the show as running a day early would make
        /schedule default to a day that has not started.

        Naive datetimes are rejected rather than assumed to be UTC or local -
        either assumption is wrong half the time and fails silently.
        """
        if now.tzinfo is None:
            raise ValueError("is_running() needs a timezone-aware datetime")
        local = now.astimezone(ZoneInfo(self.timezone)).date()
        return self.start_date <= local <= self.end_date

    def days(self) -> tuple[date, ...]:
        """Every date the show runs, first to last."""
        count = (self.end_date - self.start_date).days + 1
        return tuple(self.start_date + timedelta(days=i) for i in range(count))

    def resolve_day(self, text: str) -> date:
        """Turn a typed day into one of this show's dates.

        Accepts a date (2026-09-05), a weekday name (Saturday), or any prefix of
        one at least three letters long (Sat, Tues, Thurs), case-insensitively.
        Raises DayError otherwise - including for a real date the show does not
        cover, and for a weekday that matches two dates on a show longer than a
        week, where guessing one would be silently wrong half the time.

        One resolver shared by every command that takes a day, so /schedule,
        /find and /myschedule cannot drift apart on what they accept.
        """
        typed = text.strip()
        echo = typed[:32]
        days = self.days()
        span = (f"{self.name} runs {days[0]:%A, %b %d} "
                f"to {days[-1]:%A, %b %d}.")

        try:
            chosen = date.fromisoformat(typed)
        except ValueError:
            pass
        else:
            if chosen in days:
                return chosen
            raise DayError(f"`{echo}` is not a day of {self.name}. {span}")

        needle = typed.casefold()
        if len(needle) < _MIN_DAY_LETTERS:
            raise DayError(
                f"`{echo}` is too short to tell which day. Type at least three "
                f"letters, like Sat, or a date like {days[0].isoformat()}.")

        matches = [d for d in days if _WEEKDAYS[d.weekday()].startswith(needle)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise DayError(
                f"`{echo}` matches more than one day of {self.name}. "
                f"Use the date instead, like {matches[0].isoformat()}.")
        raise DayError(
            f"`{echo}` is not a day of {self.name}. {span} Use a day name like "
            f"Saturday, or a date like {days[0].isoformat()}.")


@dataclass(frozen=True)
class Config:
    shows: dict[str, Show]
    drop_in_threshold_minutes: int

    def show(self, slug: str) -> Show:
        try:
            return self.shows[slug]
        except KeyError:
            available = ", ".join(sorted(self.shows)) or "(none)"
            raise KeyError(f"unknown show {slug!r}; available: {available}") from None


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    shows = {
        slug: Show(
            slug=slug,
            name=body["name"],
            base_url=body["base_url"].rstrip("/"),
            api_key=body["api_key"],
            timezone=body["timezone"],
            start_date=body["start_date"],
            end_date=body["end_date"],
        )
        for slug, body in raw.get("shows", {}).items()
    }
    return Config(
        shows=shows,
        drop_in_threshold_minutes=int(
            defaults.get("drop_in_threshold_minutes", DEFAULT_DROP_IN_THRESHOLD_MINUTES)
        ),
    )
