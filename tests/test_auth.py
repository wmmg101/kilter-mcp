from __future__ import annotations

import pytest

from kilter_mcp.auth import AuthError, TokenManager
from kilter_mcp.config import Settings
from kilter_mcp.endpoints import CLIENT_ID, SCOPE
from tests.conftest import (
    ACCESS_1,
    ACCESS_2,
    REFRESH_1,
    REFRESH_2,
    TEST_PASSWORD,
    FakeKilter,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_password_grant_success(fake: FakeKilter, settings: Settings):
    tm = TokenManager(settings, fake.http(), clock=Clock())
    token = await tm.get_access_token()
    assert token == ACCESS_1
    assert tm.has_refresh_token
    call = fake.token_calls[0]
    assert call["grant_type"] == "password"
    assert call["client_id"] == CLIENT_ID
    assert call["scope"] == SCOPE


async def test_cached_token_reused_until_near_expiry(fake: FakeKilter, settings: Settings):
    clock = Clock()
    tm = TokenManager(settings, fake.http(), clock=clock)
    await tm.get_access_token()
    clock.now += 100  # 300s lifetime, 60s margin -> still valid
    assert await tm.get_access_token() == ACCESS_1
    assert len(fake.token_calls) == 1


async def test_refresh_when_expiring_and_rotation(fake: FakeKilter, settings: Settings):
    clock = Clock()
    tm = TokenManager(settings, fake.http(), clock=clock)
    await tm.get_access_token()
    clock.now += 250  # inside the 60s margin
    token = await tm.get_access_token()
    assert token == ACCESS_2
    assert fake.token_calls[1]["grant_type"] == "refresh_token"
    assert fake.token_calls[1]["refresh_token"] == REFRESH_1
    assert "password" not in fake.token_calls[1]
    # Rotated refresh token adopted: the next refresh uses REFRESH_2.
    clock.now += 250
    await tm.get_access_token()
    assert fake.token_calls[2]["refresh_token"] == REFRESH_2


async def test_refresh_without_rotation_keeps_old_refresh_token(
    fake: FakeKilter, settings: Settings
):
    fake.rotate_refresh = False
    clock = Clock()
    tm = TokenManager(settings, fake.http(), clock=clock)
    await tm.get_access_token()
    clock.now += 250
    await tm.get_access_token()
    clock.now += 250
    await tm.get_access_token()
    assert fake.token_calls[2]["refresh_token"] == REFRESH_1


async def test_refresh_failure_falls_back_to_password(fake: FakeKilter, settings: Settings):
    clock = Clock()
    tm = TokenManager(settings, fake.http(), clock=clock)
    await tm.get_access_token()
    fake.refresh_ok = False
    clock.now += 250
    token = await tm.get_access_token()
    assert token == ACCESS_2
    assert [c["grant_type"] for c in fake.token_calls] == ["password", "refresh_token", "password"]


async def test_invalid_credentials_raise_safe_error(fake: FakeKilter, settings: Settings):
    fake.password_ok = False
    tm = TokenManager(settings, fake.http(), clock=Clock())
    with pytest.raises(AuthError) as info:
        await tm.get_access_token()
    message = str(info.value)
    assert "KILTER_USERNAME" in message
    assert "invalid_grant" in message
    assert TEST_PASSWORD not in message


async def test_network_error_is_redacted(settings: Settings):
    import httpx2

    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot connect, sent password={TEST_PASSWORD}")

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(boom))
    tm = TokenManager(settings, http, clock=Clock())
    with pytest.raises(AuthError) as info:
        await tm.get_access_token()
    assert TEST_PASSWORD not in str(info.value)


async def test_repr_never_contains_tokens(fake: FakeKilter, settings: Settings):
    tm = TokenManager(settings, fake.http(), clock=Clock())
    await tm.get_access_token()
    text = repr(tm)
    assert ACCESS_1 not in text
    assert REFRESH_1 not in text
    assert TEST_PASSWORD not in text


async def test_failing_frame_locals_never_hold_secrets(fake: FakeKilter, settings: Settings):
    """pytest/debuggers print the raising frame's locals; they must not contain credentials."""
    import traceback

    fake.password_ok = False
    tm = TokenManager(settings, fake.http(), clock=Clock())
    try:
        await tm.get_access_token()
    except AuthError as exc:
        tb = exc.__traceback__
        assert tb is not None
        frames = traceback.walk_tb(tb)
        innermost = list(frames)[-1][0]
        blob = repr(innermost.f_locals)
        assert TEST_PASSWORD not in blob
        assert "grant_type" not in blob
    else:
        raise AssertionError("expected AuthError")
