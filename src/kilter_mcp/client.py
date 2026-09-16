"""HTTP client for the Kilter portal API. Knows nothing about MCP."""

from __future__ import annotations

from typing import Any

import httpx2

from kilter_mcp.auth import AuthError, TokenManager
from kilter_mcp.config import Settings, redact
from kilter_mcp.endpoints import GRADES_URL, LOGS_URL
from kilter_mcp.grades import GradeTable
from kilter_mcp.models import Grade, LogEntry

USER_AGENT = "kilter-mcp (+https://github.com/wmmg101/kilter-mcp)"


class KilterAPIError(RuntimeError):
    """The Kilter API returned an error. Message is safe to show to the agent."""


class KilterClient:
    """Read-only access to the authenticated user's Kilter data.

    ``http`` is injectable so tests can pass an ``httpx2.AsyncClient`` with a
    ``MockTransport``. The client owns the HTTP client only if it created it.
    """

    def __init__(
        self,
        settings: Settings,
        http: httpx2.AsyncClient | None = None,
        *,
        token_manager: TokenManager | None = None,
    ) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx2.AsyncClient(
            timeout=settings.timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._tokens = token_manager or TokenManager(settings, self._http)
        self._grades: GradeTable | None = None

    def __repr__(self) -> str:
        return f"KilterClient(username={self._settings.username!r})"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- public API -------------------------------------------------------------------------

    async def get_logs(self) -> list[LogEntry]:
        """Fetch every logbook row for the authenticated user, newest first."""
        payload = await self._get_authenticated(LOGS_URL)
        rows = _extract_rows(payload)
        entries = [LogEntry.from_api(row) for row in rows if isinstance(row, dict)]
        entries.sort(key=lambda e: e.created_at.timestamp() if e.created_at else 0.0, reverse=True)
        return entries

    async def get_grades(self) -> GradeTable:
        """Fetch the difficulty table (public endpoint). Cached for the process lifetime."""
        if self._grades is not None:
            return self._grades
        try:
            response = await self._http.get(GRADES_URL, headers={"Accept": "application/json"})
            response.raise_for_status()
            rows = _extract_rows(response.json())
            grades = [g for g in (Grade.from_api(r) for r in rows if isinstance(r, dict)) if g]
            if not grades:
                raise ValueError("empty grade table")
            self._grades = GradeTable.from_grades(grades, source="api")
        except (httpx2.HTTPError, ValueError):
            self._grades = GradeTable.fallback()
        return self._grades

    # -- internals --------------------------------------------------------------------------

    async def _get_authenticated(self, url: str) -> Any:
        token = await self._tokens.get_access_token()
        response = await self._request(url, token)
        if response.status_code == 401:
            # Token rejected (revoked, or expiry skew). Refresh once and retry.
            self._tokens.invalidate()
            token = await self._tokens.get_access_token()
            response = await self._request(url, token)
        if response.status_code >= 400:
            raise KilterAPIError(
                f"Kilter API request failed with HTTP {response.status_code} for {url}."
            )
        try:
            return response.json()
        except ValueError:
            raise KilterAPIError(f"Kilter API returned non-JSON content for {url}.") from None

    async def _request(self, url: str, token: str) -> httpx2.Response:
        try:
            return await self._http.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self._settings.timeout_seconds,
            )
        except AuthError:
            raise
        except httpx2.HTTPError as exc:
            raise KilterAPIError(
                f"Network error talking to Kilter: {redact(str(exc), token)}"
            ) from None


def _extract_rows(payload: Any) -> list[Any]:
    """Accept either a bare JSON array or a wrapper object with a list inside."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("logs", "data", "items", "results", "grades"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []
