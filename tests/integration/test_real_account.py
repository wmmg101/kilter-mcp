"""Opt-in smoke test against a real Kilter account.

Runs only when KILTER_INTEGRATION=1 and KILTER_USERNAME / KILTER_PASSWORD are set.
Asserts shapes only; never prints or stores log entries.
"""

from __future__ import annotations

import os

import pytest

from kilter_mcp.client import KilterClient
from kilter_mcp.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("KILTER_INTEGRATION") != "1"
    or not os.environ.get("KILTER_USERNAME")
    or not os.environ.get("KILTER_PASSWORD"),
    reason="set KILTER_INTEGRATION=1 plus KILTER_USERNAME/KILTER_PASSWORD to run",
)


async def test_real_logs_and_grades_have_expected_shape():
    client = KilterClient(Settings.from_env())
    try:
        grades = await client.get_grades()
        logs = await client.get_logs()
    finally:
        await client.aclose()
    assert grades.source == "api"
    assert len(grades.grades) >= 30
    assert isinstance(logs, list)
    for entry in logs[:20]:
        assert entry.climb_uuid
        assert entry.attempts >= 1
        assert entry.status in ("flash", "send", "attempt")
        if entry.difficulty_id is not None:
            assert grades.get(entry.difficulty_id) is not None
