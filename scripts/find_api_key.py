#!/usr/bin/env python3
"""Recover a show's LEAP API key from its public schedule page.

Keys are embedded in the page's inline JS. If a key ever rotates, this turns
an investigation into one command:

    python scripts/find_api_key.py west
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paxbot.config import load_config  # noqa: E402
from paxbot.sources.leap import USER_AGENT  # noqa: E402

KEY_RE = re.compile(r"gtAPIKey:\s*'([0-9a-fA-F-]{36})'")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: find_api_key.py <show-slug>", file=sys.stderr)
        return 2

    config = load_config(Path(__file__).resolve().parents[1] / "shows.toml")
    show = config.show(sys.argv[1])
    url = f"{show.base_url}/en-us/schedule.html"

    response = httpx.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=30, follow_redirects=True)
    response.raise_for_status()

    keys = set(KEY_RE.findall(response.text))
    if not keys:
        print(f"no gtAPIKey found at {url}", file=sys.stderr)
        return 1

    for key in sorted(keys):
        marker = "  (matches shows.toml)" if key == show.api_key else ""
        print(f"{key}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
