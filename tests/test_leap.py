from datetime import date

import httpx
import pytest

from paxbot.config import Show
from paxbot.sources.leap import USER_AGENT, LeapError, fetch_schedules

WEST = Show(
    slug="west",
    name="PAX West 2026",
    base_url="https://west.paxsite.com",
    api_key="test-key",
    timezone="America/Los_Angeles",
    start_date=date(2026, 9, 4),
    end_date=date(2026, 9, 7),
)


def client_returning(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_key_gzip_and_user_agent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        seen["ae"] = request.headers.get("accept-encoding")
        return httpx.Response(200, json={"schedules": []})

    fetch_schedules(WEST, client=client_returning(handler))

    assert "key=test-key" in seen["url"]
    assert seen["ua"] == USER_AGENT
    assert "gzip" in seen["ae"]


def test_returns_decoded_payload():
    payload = {"event_name": "PAX West 2026", "schedules": [{"id": "1"}]}
    handler = lambda request: httpx.Response(200, json=payload)
    assert fetch_schedules(WEST, client=client_returning(handler)) == payload


def test_http_error_raises_leap_error():
    handler = lambda request: httpx.Response(500, text="boom")
    with pytest.raises(LeapError) as exc:
        fetch_schedules(WEST, client=client_returning(handler))
    assert "500" in str(exc.value)


def test_auth_failure_points_at_the_key_recovery_script():
    handler = lambda request: httpx.Response(403, text="nope")
    with pytest.raises(LeapError) as exc:
        fetch_schedules(WEST, client=client_returning(handler))
    assert "find_api_key" in str(exc.value)


def test_non_json_body_raises_leap_error():
    handler = lambda request: httpx.Response(200, text="<html>maintenance</html>")
    with pytest.raises(LeapError):
        fetch_schedules(WEST, client=client_returning(handler))


def test_missing_schedules_key_raises_leap_error():
    handler = lambda request: httpx.Response(200, json={"event_name": "x"})
    with pytest.raises(LeapError) as exc:
        fetch_schedules(WEST, client=client_returning(handler))
    assert "schedules" in str(exc.value)
