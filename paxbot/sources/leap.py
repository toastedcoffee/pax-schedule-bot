"""HTTP client for the LEAP schedules API.

One request returns the entire schedule. There is no pagination and no
per-event endpoint worth using.
"""
from __future__ import annotations

import json

import httpx

from paxbot.config import Show

SCHEDULES_URL = "https://conventions.leapevent.tech/api/schedules"
USER_AGENT = (
    "paxbot/0.1 (+https://github.com/toastedcoffee/pax-schedule-bot) "
    "schedule sync; contact via GitHub issues"
)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class LeapError(Exception):
    """The API did not return a usable schedule payload."""


def fetch_schedules(show: Show, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        response = client.get(
            SCHEDULES_URL,
            params={"key": show.api_key},
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
    except httpx.RequestError as exc:
        raise LeapError(f"request to LEAP failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    if response.status_code in (401, 403):
        raise LeapError(
            f"LEAP rejected the API key for show {show.slug!r} "
            f"(HTTP {response.status_code}). Recover it with "
            f"scripts/find_api_key.py {show.slug}"
        )
    if response.status_code != 200:
        raise LeapError(f"LEAP returned HTTP {response.status_code}")

    try:
        payload = json.loads(response.text)
    except ValueError as exc:
        raise LeapError(f"LEAP response was not JSON: {exc}") from exc

    schedules = payload.get("schedules") if isinstance(payload, dict) else None
    if not isinstance(schedules, list):
        # Key-presence alone is not enough: {"schedules": null} and
        # {"schedules": "oops"} both have the key, and both escape this layer
        # to blow up in the parser as an unlabelled TypeError/AttributeError.
        # Fail here, where the error can name the API.
        raise LeapError(
            f"LEAP payload 'schedules' is {type(schedules).__name__}, expected list"
        )

    return payload
