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


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def test_429_with_retry_after_is_retried(fake: FakeKilter, settings: Settings):
    fake.rate_limit_queue = ["2", "1"]
    sleep = RecordingSleep()
    client = KilterClient(settings, fake.http(), sleep=sleep)
    logs = await client.get_logs()
    assert len(logs) == 9
    assert sleep.calls == [2.0, 1.0]
    assert sum(1 for r in fake.requests if str(r.url) == LOGS_URL) == 3


async def test_429_http_date_retry_after(fake: FakeKilter, settings: Settings):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    soon = datetime.now(timezone.utc) + timedelta(seconds=5)
    fake.rate_limit_queue = [format_datetime(soon, usegmt=True)]
    sleep = RecordingSleep()
    client = KilterClient(settings, fake.http(), sleep=sleep)
    await client.get_logs()
    assert len(sleep.calls) == 1
    assert 3.0 <= sleep.calls[0] <= 5.0


async def test_429_without_retry_after_fails_fast(fake: FakeKilter, settings: Settings):
    fake.rate_limit_queue = [None]
    sleep = RecordingSleep()
    client = KilterClient(settings, fake.http(), sleep=sleep)
    with pytest.raises(KilterAPIError, match="rate-limiting"):
        await client.get_logs()
    assert sleep.calls == []


async def test_429_retry_after_too_long_fails_fast(fake: FakeKilter, settings: Settings):
    fake.rate_limit_queue = ["600"]
    sleep = RecordingSleep()
    client = KilterClient(settings, fake.http(), sleep=sleep)
    with pytest.raises(KilterAPIError, match="rate-limiting"):
        await client.get_logs()
    assert sleep.calls == []


async def test_429_retries_are_bounded(fake: FakeKilter, settings: Settings):
    fake.rate_limit_queue = ["1", "1", "1", "1"]
    sleep = RecordingSleep()
    client = KilterClient(settings, fake.http(), sleep=sleep, max_rate_limit_retries=2)
    with pytest.raises(KilterAPIError, match="rate-limiting"):
        await client.get_logs()
    assert sleep.calls == [1.0, 1.0]


# -- logbook completeness warning ---------------------------------------------------------------


def _synthetic_rows(n: int) -> list[dict]:
    return [
        {
            "logUuid": f"test-log-{i}",
            "climbUuid": f"test-climb-{i % 7}",
            "angle": 30,
            "topped": i % 3 != 0,
            "attempts": 1,
            "createdAt": f"2026-01-{(i % 28) + 1:02d}T18:00:00Z",
            "currentDifficultyId": 13,
        }
        for i in range(n)
    ]


async def test_no_warning_for_ordinary_logbook(fake: FakeKilter, settings: Settings):
    client = KilterClient(settings, fake.http())
    assert (await client.get_logbook()).warning is None


@pytest.mark.parametrize("n", [50, 100, 500, 1000])
async def test_round_row_count_warns(fake: FakeKilter, settings: Settings, n: int):
    fake.raw_logs = _synthetic_rows(n)
    client = KilterClient(settings, fake.http())
    logbook = await client.get_logbook()
    assert len(logbook.entries) == n
    assert logbook.warning is not None
    assert f"exactly {n}" in logbook.warning
    assert "github.com/wmmg101/kilter-mcp/issues" in logbook.warning


async def test_non_round_row_count_does_not_warn(fake: FakeKilter, settings: Settings):
    fake.raw_logs = _synthetic_rows(101)
    client = KilterClient(settings, fake.http())
    assert (await client.get_logbook()).warning is None


async def test_paging_marker_warns(fake: FakeKilter, settings: Settings):
    fake.raw_logs = {"logs": _synthetic_rows(9), "nextCursor": "abc"}  # type: ignore[assignment]
    client = KilterClient(settings, fake.http())
    warning = (await client.get_logbook()).warning
    assert warning is not None and "paging marker" in warning and "nextCursor" in warning


async def test_total_larger_than_rows_warns(fake: FakeKilter, settings: Settings):
    fake.raw_logs = {"data": _synthetic_rows(9), "total": 240}  # type: ignore[assignment]
    client = KilterClient(settings, fake.http())
    warning = (await client.get_logbook()).warning
    assert warning is not None and "reports 240" in warning and "returned 9" in warning


async def test_total_equal_to_rows_is_fine(fake: FakeKilter, settings: Settings):
    fake.raw_logs = {"data": _synthetic_rows(9), "total": 9, "next": None}  # type: ignore[assignment]
    client = KilterClient(settings, fake.http())
    assert (await client.get_logbook()).warning is None
