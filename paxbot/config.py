"""Show configuration. Adding a PAX is a block in shows.toml, not code."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DROP_IN_THRESHOLD_MINUTES = 240


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
