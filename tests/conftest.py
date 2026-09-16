"""Shared fixtures: synthetic data and a fake Kilter backend over httpx2.MockTransport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest

from kilter_mcp.config import Settings
from kilter_mcp.endpoints import GRADES_URL, LOGS_URL, TOKEN_URL
from kilter_mcp.grades import GradeTable
from kilter_mcp.models import Grade, LogEntry

FIXTURES = Path(__file__).parent / "fixtures"

TEST_USERNAME = "test-user@example.invalid"
TEST_PASSWORD = "test-password"
ACCESS_1 = "test-access-token-1"
ACCESS_2 = "test-access-token-2"
REFRESH_1 = "test-refresh-token-1"
REFRESH_2 = "test-refresh-token-2"


@pytest.fixture
def raw_logs() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "logs.json").read_text())


@pytest.fixture
def raw_grades() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "grades.json").read_text())


@pytest.fixture
def logs(raw_logs: list[dict[str, Any]]) -> list[LogEntry]:
    return [LogEntry.from_api(r) for r in raw_logs]


@pytest.fixture
def grades(raw_grades: list[dict[str, Any]]) -> GradeTable:
    return GradeTable.from_grades([g for g in map(Grade.from_api, raw_grades) if g])


@pytest.fixture
def settings() -> Settings:
    return Settings(username=TEST_USERNAME, password=TEST_PASSWORD)


@dataclass
class FakeKilter:
    """Scriptable fake for Keycloak + portal. Records every request for assertions."""

    raw_logs: list[dict[str, Any]]
    raw_grades: list[dict[str, Any]]
    password_ok: bool = True
    refresh_ok: bool = True
    rotate_refresh: bool = True
    expires_in: int = 300
    grades_status: int = 200
    logs_status: int = 200
    reject_tokens: set[str] = field(default_factory=set)
    # Each entry is a Retry-After header value (or None for no header) to send with a 429
    # before the logs endpoint starts answering 200. Consumed in order.
    rate_limit_queue: list[str | None] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)
    token_calls: list[dict[str, str]] = field(default_factory=list)
    _issued: int = 0

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=self.transport())

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == TOKEN_URL:
            return self._token(request)
        if url == GRADES_URL:
            if self.grades_status != 200:
                return httpx2.Response(self.grades_status, json={"error": "nope"})
            return httpx2.Response(200, json=self.raw_grades)
        if url == LOGS_URL:
            auth = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if not token or token in self.reject_tokens:
                return httpx2.Response(401, json={"error": "unauthorized"})
            if self.rate_limit_queue:
                retry_after = self.rate_limit_queue.pop(0)
                headers = {} if retry_after is None else {"Retry-After": retry_after}
                return httpx2.Response(429, headers=headers, json={"error": "rate limited"})
            if self.logs_status != 200:
                return httpx2.Response(self.logs_status, json={"error": "boom"})
            return httpx2.Response(200, json=self.raw_logs)
        return httpx2.Response(404, json={"error": "unknown endpoint"})

    def _token(self, request: httpx2.Request) -> httpx2.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_calls.append(form)
        grant = form.get("grant_type")
        if grant == "password":
            ok = (
                self.password_ok
                and form.get("username") == TEST_USERNAME
                and form.get("password") == TEST_PASSWORD
            )
            if not ok:
                return httpx2.Response(
                    401,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Invalid user credentials",
                    },
                )
        elif grant == "refresh_token":
            if not self.refresh_ok or form.get("refresh_token") not in (REFRESH_1, REFRESH_2):
                return httpx2.Response(
                    400, json={"error": "invalid_grant", "error_description": "Token is not active"}
                )
        else:
            return httpx2.Response(400, json={"error": "unsupported_grant_type"})
        self._issued += 1
        access = ACCESS_1 if self._issued == 1 else ACCESS_2
        body: dict[str, Any] = {
            "access_token": access,
            "expires_in": self.expires_in,
            "token_type": "Bearer",
        }
        if grant == "password" or self.rotate_refresh:
            body["refresh_token"] = REFRESH_1 if self._issued == 1 else REFRESH_2
        return httpx2.Response(200, json=body)


@pytest.fixture
def fake(raw_logs: list[dict[str, Any]], raw_grades: list[dict[str, Any]]) -> FakeKilter:
    return FakeKilter(raw_logs=raw_logs, raw_grades=raw_grades)
