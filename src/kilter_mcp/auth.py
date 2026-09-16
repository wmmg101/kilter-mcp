"""Keycloak password-grant authentication with in-memory token refresh.

Tokens live only in this object for the lifetime of the MCP process. Nothing is written to
disk and nothing here is ever logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx2

from kilter_mcp.config import Settings, redact
from kilter_mcp.endpoints import CLIENT_ID, SCOPE, TOKEN_URL

# Refresh this many seconds before the access token actually expires.
EXPIRY_MARGIN_SECONDS = 60.0

# After a rejected password grant, don't try again for this long. One agent question fans out
# into several tool calls; without this, a mistyped password becomes a burst of failed logins
# that can trip Keycloak's brute-force protection and lock the user's Kilter account.
LOGIN_FAILURE_COOLDOWN_SECONDS = 60.0


class AuthError(RuntimeError):
    """Authentication with Kilter failed. Message is safe to show to the agent.

    ``rejected`` is True when Keycloak answered and refused the grant (bad credentials,
    disabled account), as opposed to a network failure or a malformed response.
    """

    def __init__(self, message: str, *, rejected: bool = False) -> None:
        super().__init__(message)
        self.rejected = rejected


def _is_credential_rejection(exc: AuthError) -> bool:
    return exc.rejected


class TokenManager:
    def __init__(
        self,
        settings: Settings,
        http: httpx2.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        login_failure_cooldown: float = LOGIN_FAILURE_COOLDOWN_SECONDS,
    ) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._cooldown = login_failure_cooldown
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._login_blocked_until: float = 0.0
        self._last_login_error: str | None = None

    def __repr__(self) -> str:
        state = "authenticated" if self._access_token else "unauthenticated"
        return f"TokenManager({state})"

    @property
    def has_refresh_token(self) -> bool:
        return self._refresh_token is not None

    def _redact(self, text: str) -> str:
        return redact(
            text,
            self._settings.password,
            self._access_token or "",
            self._refresh_token or "",
        )

    async def get_access_token(self) -> str:
        """Return a valid access token, logging in or refreshing as needed."""
        if self._access_token and self._clock() < self._expires_at - EXPIRY_MARGIN_SECONDS:
            return self._access_token
        if self._refresh_token:
            try:
                await self._refresh()
                assert self._access_token is not None
                return self._access_token
            except AuthError:
                # Refresh token expired or was revoked; fall back to a fresh login.
                self._refresh_token = None
        await self._password_grant()
        assert self._access_token is not None
        return self._access_token

    def invalidate(self) -> None:
        """Force a refresh on the next call (e.g. after a 401 from the API)."""
        self._expires_at = 0.0

    async def _password_grant(self) -> None:
        now = self._clock()
        if self._last_login_error is not None and now < self._login_blocked_until:
            remaining = int(self._login_blocked_until - now) + 1
            raise AuthError(
                f"{self._last_login_error} Not retrying for {remaining}s to protect the "
                "account from lockout; fix the credentials and try again after that."
            )
        try:
            await self._token_request(
                {
                    "grant_type": "password",
                    "client_id": CLIENT_ID,
                    "username": self._settings.username,
                    "password": self._settings.password,
                    "scope": SCOPE,
                },
                failure_hint=(
                    "Kilter login failed. Check KILTER_USERNAME and KILTER_PASSWORD in your "
                    "Kiro MCP configuration."
                ),
            )
        except AuthError as exc:
            if _is_credential_rejection(exc):
                self._last_login_error = str(exc)
                self._login_blocked_until = self._clock() + self._cooldown
            raise
        self._last_login_error = None
        self._login_blocked_until = 0.0

    async def _refresh(self) -> None:
        await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._refresh_token,
            },
            failure_hint="Kilter token refresh failed.",
        )

    async def _token_request(self, data: dict[str, Any], *, failure_hint: str) -> None:
        # Deliberately split in two: this frame holds the form data (password / refresh token)
        # and never raises, so a traceback's "failing frame" arguments can't expose them.
        # Debuggers and test runners (pytest) print those arguments by default.
        outcome = await self._post_form(data)
        self._apply_token_outcome(outcome, failure_hint)

    async def _post_form(self, data: dict[str, Any]) -> tuple[int, Any, str]:
        """POST to the token endpoint. Returns (status, json_or_None, redacted_error)."""
        try:
            response = await self._http.post(
                TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
                timeout=self._settings.timeout_seconds,
            )
        except httpx2.HTTPError as exc:
            return 0, None, self._redact(str(exc))
        try:
            return response.status_code, response.json(), ""
        except ValueError:
            return response.status_code, None, ""

    def _apply_token_outcome(self, outcome: tuple[int, Any, str], failure_hint: str) -> None:
        status, payload, network_error = outcome
        if status == 0:
            raise AuthError(f"{failure_hint} Network error: {network_error}") from None
        if status != 200:
            # 400/401 from Keycloak means it evaluated and refused the grant; 5xx/429 do not.
            raise AuthError(
                f"{failure_hint} ({status}{_error_detail(payload)})",
                rejected=status in (400, 401, 403),
            ) from None
        if not isinstance(payload, dict):
            raise AuthError(f"{failure_hint} Unexpected non-JSON response.") from None

        access = payload.get("access_token")
        if not isinstance(access, str) or not access:
            raise AuthError(f"{failure_hint} Response contained no access token.") from None

        self._access_token = access
        # Kilter rotates refresh tokens; adopt the new one when present.
        new_refresh = payload.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            self._refresh_token = new_refresh
        expires_in = payload.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 300.0
        self._expires_at = self._clock() + lifetime


def _error_detail(payload: Any) -> str:
    """Extract Keycloak's error code (never its full body, which could echo input)."""
    if isinstance(payload, dict):
        code = payload.get("error")
        desc = payload.get("error_description")
        parts = [p for p in (code, desc) if isinstance(p, str)]
        if parts:
            return ": " + " - ".join(parts)
    return ""
