from __future__ import annotations

import pytest

from kilter_mcp.auth import AuthError
from kilter_mcp.client import KilterAPIError, KilterClient
from kilter_mcp.config import Settings
from kilter_mcp.endpoints import GRADES_URL, LOGS_URL, TOKEN_URL
from tests.conftest import ACCESS_1, ACCESS_2, FakeKilter


async def test_get_logs_parses_and_sorts_newest_first(fake: FakeKilter, settings: Settings):
    client = KilterClient(settings, fake.http())
    logs = await client.get_logs()
    assert len(logs) == 9
    assert logs[0].log_uuid == "test-log-9"
    assert logs[-1].log_uuid == "test-log-1"
    log_request = next(r for r in fake.requests if str(r.url) == LOGS_URL)
    assert log_request.headers["Authorization"] == f"Bearer {ACCESS_1}"
    assert log_request.headers["Accept"] == "application/json"


async def test_get_logs_accepts_wrapped_payload(fake: FakeKilter, settings: Settings):
    fake.raw_logs = {"logs": fake.raw_logs}  # type: ignore[assignment]
    client = KilterClient(settings, fake.http())
    assert len(await client.get_logs()) == 9


async def test_401_triggers_refresh_and_retry(fake: FakeKilter, settings: Settings):
    fake.reject_tokens = {ACCESS_1}
    client = KilterClient(settings, fake.http())
    logs = await client.get_logs()
    assert logs
    grants = [c["grant_type"] for c in fake.token_calls]
    assert grants == ["password", "refresh_token"]
    log_requests = [r for r in fake.requests if str(r.url) == LOGS_URL]
    assert [r.headers["Authorization"] for r in log_requests] == [
        f"Bearer {ACCESS_1}",
        f"Bearer {ACCESS_2}",
    ]


async def test_api_error_message_has_no_token(fake: FakeKilter, settings: Settings):
    fake.logs_status = 500
    client = KilterClient(settings, fake.http())
    with pytest.raises(KilterAPIError) as info:
        await client.get_logs()
    assert "500" in str(info.value)
    assert ACCESS_1 not in str(info.value)


async def test_auth_failure_propagates(fake: FakeKilter, settings: Settings):
    fake.password_ok = False
    client = KilterClient(settings, fake.http())
    with pytest.raises(AuthError):
        await client.get_logs()


async def test_get_grades_from_api_and_cached(fake: FakeKilter, settings: Settings):
    client = KilterClient(settings, fake.http())
    table = await client.get_grades()
    assert table.source == "api"
    assert table.v_grade(16) == "V3"
    await client.get_grades()
    assert sum(1 for r in fake.requests if str(r.url) == GRADES_URL) == 1
    # Public endpoint: no token requested, no Authorization header sent.
    assert not any(str(r.url) == TOKEN_URL for r in fake.requests)
    grade_req = next(r for r in fake.requests if str(r.url) == GRADES_URL)
    assert "Authorization" not in grade_req.headers


async def test_get_grades_falls_back_when_endpoint_fails(fake: FakeKilter, settings: Settings):
    fake.grades_status = 503
    client = KilterClient(settings, fake.http())
    table = await client.get_grades()
    assert table.source == "fallback"
    assert table.v_grade(22) == "V6"


async def test_repr_has_no_secrets(fake: FakeKilter, settings: Settings):
    client = KilterClient(settings, fake.http())
    await client.get_logs()
    assert ACCESS_1 not in repr(client)
    assert settings.password not in repr(client)
