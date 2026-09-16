"""Settings read from the environment that Kiro passes to the MCP process."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

USERNAME_VAR = "KILTER_USERNAME"
PASSWORD_VAR = "KILTER_PASSWORD"

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
