import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from paxbot.config import Show
from paxbot.sources.parse import clean_text, parse_schedules

FIXTURES = Path(__file__).parent / "fixtures"

WEST = Show(
    slug="west",
    name="PAX West 2026",
    base_url="https://west.paxsite.com",
    api_key="key",
    timezone="America/Los_Angeles",
    start_date=date(2026, 9, 4),
    end_date=date(2026, 9, 7),
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def parsed():
    return parse_schedules(load("schedules_sample.json"), WEST)


def by_id(result, gt_id):
    return next(e for e in result.events if e.gt_id == gt_id)


def test_parses_every_good_record(parsed):
    assert len(parsed.events) == 5
    assert parsed.skipped == []


def test_local_time_converted_to_utc(parsed):
    # 11:30 PDT (UTC-7) == 18:30 UTC
    assert by_id(parsed, "943694").starts_at == datetime(2026, 9, 4, 18, 30, tzinfo=UTC)
    assert by_id(parsed, "943694").ends_at == datetime(2026, 9, 4, 22, 0, tzinfo=UTC)


def test_day_is_the_show_local_calendar_date(parsed):
    assert by_id(parsed, "943694").day == date(2026, 9, 4)
    assert by_id(parsed, "943681").day == date(2026, 9, 7)


def test_html_entities_are_unescaped(parsed):
    assert by_id(parsed, "943678").location == "Crokinole & KLASK Zone (LVL 0)"


def test_html_tags_stripped_from_description(parsed):
    desc = by_id(parsed, "943678").description
    assert "<br" not in desc
    assert desc == "Play two of the world's greatest dexterity games. Boards available."


def test_empty_description_becomes_empty_string(parsed):
    assert by_id(parsed, "949290").description == ""


def test_categories_captured(parsed):
    assert by_id(parsed, "943694").categories == (
        "Tabletop", "Tabletop Tournaments", "Tournaments",
    )


def test_url_points_at_the_public_event_page(parsed):
    assert by_id(parsed, "943694").url == (
        "https://west.paxsite.com/en-us/schedule/schedule-item.html?gtID=943694"
    )


def test_no_end_time_is_ignored_entirely(parsed):
    """Both spellings are falsy upstream; end_time is always real."""
    false_flagged = by_id(parsed, "945852")   # no_end_time: false
    null_flagged = by_id(parsed, "943678")    # no_end_time: null
    assert false_flagged.ends_at is not None
    assert null_flagged.ends_at is not None
    assert false_flagged.duration_minutes == 825
    assert null_flagged.duration_minutes == 810


def test_long_open_play_is_a_drop_in(parsed):
    assert by_id(parsed, "943678").is_drop_in(240) is True
    assert by_id(parsed, "943694").is_drop_in(240) is False


def test_row_hash_present_and_distinct(parsed):
    hashes = {e.row_hash for e in parsed.events}
    assert len(hashes) == len(parsed.events)


def test_bad_records_are_skipped_not_fatal():
    result = parse_schedules(load("edge_malformed.json"), WEST)
    assert [e.gt_id for e in result.events] == ["1"]
    assert len(result.skipped) == 3
    reasons = " ".join(s.reason for s in result.skipped)
    assert "start_time" in reasons
    assert "title" in reasons
    assert "id" in reasons


def test_clean_text_handles_none():
    assert clean_text(None) == ""
