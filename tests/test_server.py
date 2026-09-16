"""End-to-end tests of the MCP tools, in-process, against the fake Kilter backend."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from kilter_mcp.client import KilterClient
from kilter_mcp.config import ConfigError, Settings
from kilter_mcp.server import KilterService, create_server
from tests.conftest import ACCESS_1, REFRESH_1, TEST_PASSWORD, FakeKilter

EXPECTED_TOOLS = {
    "kilter_get_logs",
    "kilter_get_sends",
    "kilter_get_projects",
    "kilter_get_summary",
    "kilter_get_grade_pyramid",
    "kilter_get_hardest_sends",
    "kilter_get_sessions",
    "kilter_get_progression",
    "kilter_get_angle_stats",
}


@pytest.fixture
def server(fake: FakeKilter, settings: Settings):
    service = KilterService(lambda: KilterClient(settings, fake.http()))
    return create_server(service)


def _payload(result) -> Any:
    assert not result.is_error, result.content[0].text
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def test_tools_are_listed_and_read_only(server):
    async with Client(server) as client:
        listed = await client.list_tools()
    names = {t.name for t in listed.tools}
    assert names == EXPECTED_TOOLS
    for tool in listed.tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert tool.description and "Use " in tool.description
        assert "\n        " not in tool.description  # docstrings are dedented


async def test_get_logs_filters_and_shape(server):
    async with Client(server) as client:
        res = _payload(await client.call_tool("kilter_get_logs", {"limit": 2, "angle": 30}))
    assert res["total_matching"] == 6
    assert res["returned"] == 2
    entry = res["entries"][0]
    assert set(entry) == {
        "climb_name",
        "date",
        "angle",
        "attempts",
        "topped",
        "flashed",
        "status",
        "difficulty_id",
        "grade",
        "font_grade",
        "my_difficulty_id",
        "my_grade",
        "my_rating",
        "climb_uuid",
    }
    assert res["timezone"] == "UTC"


async def test_get_logs_date_range_and_topped(server):
    async with Client(server) as client:
        res = _payload(
            await client.call_tool(
                "kilter_get_logs",
                {"start_date": "2026-01-08", "end_date": "2026-01-08", "topped": False},
            )
        )
    assert [e["climb_name"] for e in res["entries"]] == ["Project Climb"]


async def test_get_logs_bad_date_is_tool_error(server):
    async with Client(server) as client:
        res = await client.call_tool("kilter_get_logs", {"start_date": "last tuesday"})
    assert res.is_error
    assert "YYYY-MM-DD" in res.content[0].text


async def test_get_sends(server):
    async with Client(server) as client:
        res = _payload(await client.call_tool("kilter_get_sends", {}))
    assert res["total_matching"] == 6
    assert all(s["topped"] for s in res["sends"])


async def test_get_projects(server):
    async with Client(server) as client:
        res = _payload(await client.call_tool("kilter_get_projects", {"angle": 30}))
    assert res["total_projects"] == 1
    assert res["projects"][0]["climb_name"] == "Project Climb"


async def test_get_summary(server):
    async with Client(server) as client:
        res = _payload(await client.call_tool("kilter_get_summary", {}))
    assert res["sends"] == 6
    assert res["hardest_send"]["grade"] == "V4"


async def test_other_tools_return_structured_data(server):
    async with Client(server) as client:
        pyramid = _payload(await client.call_tool("kilter_get_grade_pyramid", {}))
        hardest = _payload(await client.call_tool("kilter_get_hardest_sends", {"limit": 3}))
        sess = _payload(await client.call_tool("kilter_get_sessions", {"limit": 2}))
        prog = _payload(await client.call_tool("kilter_get_progression", {"period": "week"}))
        angles = _payload(await client.call_tool("kilter_get_angle_stats", {}))
    assert pyramid["levels"][0]["grade"] == "V4"
    assert len(hardest["hardest_sends"]) == 3
    assert len(sess["sessions"]) == 2
    assert prog["periods"] and prog["period"] == "week"
    assert [a["angle"] for a in angles["angles"]] == [20, 30, 40]


async def test_invalid_period_is_tool_error(server):
    async with Client(server) as client:
        res = await client.call_tool("kilter_get_progression", {"period": "decade"})
    assert res.is_error and "period" in res.content[0].text


async def test_missing_config_is_actionable_error():
    def factory():
        raise ConfigError("Kilter credentials are not configured. Set KILTER_USERNAME ...")

    server = create_server(KilterService(factory))
    async with Client(server) as client:
        res = await client.call_tool("kilter_get_summary", {})
    assert res.is_error
    assert "KILTER_USERNAME" in res.content[0].text


async def test_auth_failure_is_reported_without_secrets(fake: FakeKilter, settings: Settings):
    fake.password_ok = False
    server = create_server(KilterService(lambda: KilterClient(settings, fake.http())))
    async with Client(server) as client:
        res = await client.call_tool("kilter_get_sends", {})
    assert res.is_error
    text = res.content[0].text
    assert "login failed" in text.lower()
    assert TEST_PASSWORD not in text


async def test_no_secrets_in_any_tool_output(server):
    async with Client(server) as client:
        listed = await client.list_tools()
        outputs = []
        for tool in listed.tools:
            result = await client.call_tool(tool.name, {})
            outputs.append(json.dumps(result.model_dump(mode="json")))
    blob = "\n".join(outputs)
    for secret in (TEST_PASSWORD, ACCESS_1, REFRESH_1, "Bearer", "test-user", "test-gym"):
        assert secret not in blob


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _log_fetches(fake: FakeKilter) -> int:
    from kilter_mcp.endpoints import LOGS_URL

    return sum(1 for r in fake.requests if str(r.url) == LOGS_URL)


async def test_logs_are_cached_across_tool_calls(fake: FakeKilter, settings: Settings):
    clock = FakeClock()
    service = KilterService(
        lambda: KilterClient(settings, fake.http()), cache_ttl=60.0, clock=clock
    )
    server = create_server(service)
    async with Client(server) as client:
        await client.call_tool("kilter_get_summary", {})
        await client.call_tool("kilter_get_sends", {})
        await client.call_tool("kilter_get_projects", {})
        assert _log_fetches(fake) == 1
        clock.now += 61
        await client.call_tool("kilter_get_sessions", {})
        assert _log_fetches(fake) == 2


async def test_concurrent_tool_calls_share_one_fetch(fake: FakeKilter, settings: Settings):
    import asyncio

    service = KilterService(lambda: KilterClient(settings, fake.http()))
    server = create_server(service)
    async with Client(server) as client:
        await asyncio.gather(
            client.call_tool("kilter_get_summary", {}),
            client.call_tool("kilter_get_angle_stats", {}),
            client.call_tool("kilter_get_grade_pyramid", {}),
        )
    assert _log_fetches(fake) == 1


async def test_failed_fetch_is_not_cached(fake: FakeKilter, settings: Settings):
    fake.logs_status = 500
    service = KilterService(lambda: KilterClient(settings, fake.http()))
    server = create_server(service)
    async with Client(server) as client:
        first = await client.call_tool("kilter_get_summary", {})
        assert first.is_error
        fake.logs_status = 200
        second = await client.call_tool("kilter_get_summary", {})
        assert not second.is_error


async def test_kilter_timezone_env_changes_sessions(
    fake: FakeKilter, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    # 2026-02-10T19:00Z and 19:30Z / 19:45Z are still 10 Feb in Pacific/Auckland (+13 → 08:00
    # next day!). Use that to prove the env var is honoured end to end.
    monkeypatch.setenv("KILTER_TIMEZONE", "Pacific/Auckland")
    server = create_server(KilterService(lambda: KilterClient(settings, fake.http())))
    async with Client(server) as client:
        res = _payload(await client.call_tool("kilter_get_sessions", {"limit": 1}))
    assert res["timezone"] == "Pacific/Auckland"
    assert res["sessions"][0]["date"] == "2026-02-11"
    assert res["sessions"][0]["climbs"][0]["date"].endswith("+13:00")


async def test_invalid_kilter_timezone_is_tool_error(
    fake: FakeKilter, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KILTER_TIMEZONE", "Mars/Olympus_Mons")
    server = create_server(KilterService(lambda: KilterClient(settings, fake.http())))
    async with Client(server) as client:
        res = await client.call_tool("kilter_get_summary", {})
    assert res.is_error
    assert "KILTER_TIMEZONE" in res.content[0].text
    assert "Europe/Rome" in res.content[0].text


# -- --check diagnostic -------------------------------------------------------------------------


async def test_check_reports_counts_without_secrets(
    fake: FakeKilter, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    from kilter_mcp.server import run_check
    from tests.conftest import TEST_USERNAME

    monkeypatch.setenv("KILTER_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("KILTER_PASSWORD", TEST_PASSWORD)
    code, report = await run_check(KilterService(lambda: KilterClient(settings, fake.http())))
    assert code == 0
    assert "result:    ok" in report
    assert "9 entries, 6 sends, 4 sessions, most recent 2026-02-10" in report
    assert "39 grades from api" in report
    assert "timezone:  UTC" in report
    assert "te***@example.invalid" in report
    for secret in (TEST_PASSWORD, ACCESS_1, REFRESH_1, TEST_USERNAME, "Test Climb"):
        assert secret not in report


async def test_check_reports_missing_config(monkeypatch: pytest.MonkeyPatch):
    from kilter_mcp.server import run_check

    monkeypatch.delenv("KILTER_USERNAME", raising=False)
    monkeypatch.delenv("KILTER_PASSWORD", raising=False)
    code, report = await run_check()
    assert code == 1
    assert "config:    FAILED" in report
    assert "KILTER_USERNAME" in report


async def test_check_reports_login_failure(
    fake: FakeKilter, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    from kilter_mcp.server import run_check
    from tests.conftest import TEST_USERNAME

    monkeypatch.setenv("KILTER_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("KILTER_PASSWORD", TEST_PASSWORD)
    fake.password_ok = False
    code, report = await run_check(KilterService(lambda: KilterClient(settings, fake.http())))
    assert code == 1
    assert "kilter:    FAILED" in report
    assert "login failed" in report.lower()
    assert TEST_PASSWORD not in report


def test_main_check_flag_exits_with_code(monkeypatch: pytest.MonkeyPatch, capsys):
    from kilter_mcp.server import main

    monkeypatch.delenv("KILTER_USERNAME", raising=False)
    monkeypatch.delenv("KILTER_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as info:
        main(["--check"])
    assert info.value.code == 1
    assert "config:    FAILED" in capsys.readouterr().out


async def test_every_tool_parameter_is_described(server):
    async with Client(server) as client:
        listed = await client.list_tools()
    missing = [
        f"{tool.name}.{name}"
        for tool in listed.tools
        for name, prop in (tool.input_schema.get("properties") or {}).items()
        if not prop.get("description")
    ]
    assert missing == []
