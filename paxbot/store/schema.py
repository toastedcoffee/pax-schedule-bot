"""Schema DDL.

Every row is keyed so that a hosted multi-guild instance needs no migration:
schedules belong to a user, visibility belongs to a (guild, user) pair.
Phase 1 only creates the tables the data layer needs.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    show_slug   TEXT NOT NULL,
    gt_id       TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    day         TEXT NOT NULL,          -- ISO date, show-local
    starts_at   TEXT NOT NULL,          -- ISO 8601 UTC
    ends_at     TEXT NOT NULL,          -- ISO 8601 UTC, always real
    location    TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    cancelled   INTEGER NOT NULL DEFAULT 0,
    row_hash    TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (show_slug, gt_id)
);

CREATE INDEX IF NOT EXISTS idx_events_day
    ON events (show_slug, day, starts_at);

CREATE TABLE IF NOT EXISTS event_categories (
    show_slug TEXT NOT NULL,
    gt_id     TEXT NOT NULL,
    category  TEXT NOT NULL,
    ordinal   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (show_slug, gt_id, category)
);

CREATE INDEX IF NOT EXISTS idx_event_categories_lookup
    ON event_categories (show_slug, category);

CREATE TABLE IF NOT EXISTS sync_runs (
    show_slug    TEXT NOT NULL,
    ran_at       TEXT NOT NULL,
    event_count  INTEGER NOT NULL,
    ok           INTEGER NOT NULL,
    note         TEXT NOT NULL DEFAULT ''
);
"""
