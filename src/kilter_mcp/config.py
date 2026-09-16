"""Settings read from the environment that Kiro passes to the MCP process."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

USERNAME_VAR = "KILTER_USERNAME"
PASSWORD_VAR = "KILTER_PASSWORD"
TIMEZONE_VAR = "KILTER_TIMEZONE"

MISSING_CONFIG_MESSAGE = (
    "Kilter credentials are not configured. Set KILTER_USERNAME and KILTER_PASSWORD "
    'in the "env" section of the kilter-mcp entry in your Kiro mcp.json '
    "(see https://kiro.dev/docs/mcp/configuration)."
)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    username: str
    password: str = field(repr=False)
    timeout_seconds: float = 30.0

    def __repr__(self) -> str:  # never print the password
        return f"Settings(username={self.username!r}, password='***')"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        username = (source.get(USERNAME_VAR) or "").strip()
        password = source.get(PASSWORD_VAR) or ""
        if not username or not password:
            raise ConfigError(MISSING_CONFIG_MESSAGE)
        return cls(username=username, password=password)


def resolve_timezone(env: Mapping[str, str] | None = None) -> tzinfo:
    """Timezone used to decide which calendar day a climb belongs to.

    Order: ``KILTER_TIMEZONE`` (IANA name such as ``Europe/Rome``), then the host's zone
    (``TZ`` env var, or the ``/etc/localtime`` symlink on macOS/Linux), then the host's
    current fixed UTC offset. Independent of credentials so a bad value is reported clearly.
    """
    source = os.environ if env is None else env
    explicit = (source.get(TIMEZONE_VAR) or "").strip()
    if explicit:
        try:
            return ZoneInfo(explicit)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(
                f"{TIMEZONE_VAR}={explicit!r} is not a valid IANA timezone name "
                "(examples: Europe/Rome, America/Denver, UTC)."
            ) from None
    for candidate in (_tz_from_env(source), _tz_from_localtime()):
        if candidate is not None:
            return candidate
    return datetime.now().astimezone().tzinfo or timezone.utc


def timezone_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str):
        return key
    name = tz.tzname(datetime.now(tz))
    return name or "UTC"


def _tz_from_env(source: Mapping[str, str]) -> tzinfo | None:
    name = (source.get("TZ") or "").strip().lstrip(":")
    if not name or ("/" not in name and name.upper() != "UTC"):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _tz_from_localtime() -> tzinfo | None:
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    idx = target.rfind(marker)
    if idx == -1:
        return None
    name = target[idx + len(marker) :]
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")
_TOKEN_FIELD_RE = re.compile(
    r'(?i)("?(?:access_token|refresh_token|id_token|password)"?\s*[:=]\s*)("?)[^",\s&]+(\2)'
)


def redact(text: str, *secrets: str) -> str:
    """Remove bearer tokens, token/password fields and any given secret values from text."""
    out = _BEARER_RE.sub("Bearer ***", text)
    out = _TOKEN_FIELD_RE.sub(r"\1\2***\3", out)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out
