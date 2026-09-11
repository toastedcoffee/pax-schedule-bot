#!/usr/bin/env python3
"""Re-capture the trimmed test fixture from the live API.

Keeps only a handful of representative events. The repo tests a parser; it
does not republish PAX's schedule.

    python scripts/refresh_fixtures.py west
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paxbot.config import load_config  # noqa: E402
from paxbot.sources.leap import fetch_schedules  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "schedules_sample.json"
KEEP_FIELDS = (
    "id", "title", "description", "start_time", "end_time",
    "no_end_time", "location", "venue_location", "schedule_categories",
)
MAX_DESCRIPTION = 150


def trim(record: dict) -> dict:
    out = {k: record.get(k) for k in KEEP_FIELDS}
    out["description"] = (record.get("description") or "")[:MAX_DESCRIPTION]
    out["schedule_categories"] = [
        {"id": c["id"], "name": c["name"]}
        for c in record.get("schedule_categories") or []
    ]
    return out


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "west"
    show = load_config(ROOT / "shows.toml").show(slug)
    payload = fetch_schedules(show)
    records = payload["schedules"]

    picks: list[dict] = []
    seen: set[str] = set()

    def take(pred):
        for r in records:
            if r["id"] not in seen and pred(r):
                picks.append(r)
                seen.add(r["id"])
                return

    take(lambda r: r.get("no_end_time") is False)
    take(lambda r: r.get("no_end_time") is None)
    take(lambda r: "&" in (r.get("location") or ""))
    take(lambda r: "<" in (r.get("description") or ""))
    take(lambda r: not r.get("description"))
    take(lambda r: len(r.get("schedule_categories") or []) >= 3)

    out = {
        "event_id": payload.get("event_id"),
        "event_name": payload.get("event_name"),
        "event_slug": payload.get("event_slug"),
        "schedules": [trim(r) for r in picks],
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(picks)} events to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
