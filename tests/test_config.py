from __future__ import annotations

import pytest

from kilter_mcp.config import ConfigError, Settings, redact


def test_from_env_reads_credentials():
    s = Settings.from_env({"KILTER_USERNAME": " someone ", "KILTER_PASSWORD": "pw"})
    assert s.username == "someone"
    assert s.password == "pw"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"KILTER_USERNAME": "someone"},
        {"KILTER_PASSWORD": "pw"},
        {"KILTER_USERNAME": "", "KILTER_PASSWORD": "pw"},
        {"KILTER_USERNAME": "someone", "KILTER_PASSWORD": ""},
    ],
)
def test_missing_config_raises_actionable_error(env):
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env)
    assert "KILTER_USERNAME" in str(info.value)
    assert "KILTER_PASSWORD" in str(info.value)


def test_repr_hides_password():
    s = Settings(username="someone", password="super-secret")
    assert "super-secret" not in repr(s)
    assert "super-secret" not in str(s)
    assert "someone" in repr(s)


def test_redact_bearer_and_token_fields():
    text = (
        'Authorization: Bearer abc.def-ghi request failed {"access_token": "tok1", '
        '"refresh_token":"tok2"} password=hunter2&grant_type=password'
    )
    out = redact(text)
    assert "abc.def-ghi" not in out
    assert "tok1" not in out
    assert "tok2" not in out
    assert "hunter2" not in out
    assert "grant_type=password" in out  # ordinary words survive


def test_redact_explicit_secrets():
    out = redact("error for user with pw s3cr3t and token XYZ", "s3cr3t", "XYZ", "")
    assert "s3cr3t" not in out
    assert "XYZ" not in out


def test_resolve_timezone_explicit():
    from zoneinfo import ZoneInfo

    from kilter_mcp.config import resolve_timezone, timezone_name

    tz = resolve_timezone({"KILTER_TIMEZONE": " Europe/Rome "})
    assert tz == ZoneInfo("Europe/Rome")
    assert timezone_name(tz) == "Europe/Rome"


def test_resolve_timezone_invalid_is_config_error():
    from kilter_mcp.config import resolve_timezone

    with pytest.raises(ConfigError, match="KILTER_TIMEZONE"):
        resolve_timezone({"KILTER_TIMEZONE": "Nowhere/Land"})


def test_resolve_timezone_falls_back_to_tz_env_then_host():
    from datetime import tzinfo
    from zoneinfo import ZoneInfo

    from kilter_mcp.config import resolve_timezone

    assert resolve_timezone({"TZ": "America/Denver"}) == ZoneInfo("America/Denver")
    assert resolve_timezone({"TZ": ":Asia/Tokyo"}) == ZoneInfo("Asia/Tokyo")
    # POSIX-style TZ strings are not IANA names; skipped, host fallback still yields a tzinfo.
    assert isinstance(resolve_timezone({"TZ": "EST5EDT"}), tzinfo)
    assert isinstance(resolve_timezone({}), tzinfo)


def test_timezone_name_for_fixed_offset():
    from datetime import timedelta, timezone

    from kilter_mcp.config import timezone_name

    assert timezone_name(timezone.utc) == "UTC"
    assert timezone_name(timezone(timedelta(hours=2))) == "UTC+02:00"
