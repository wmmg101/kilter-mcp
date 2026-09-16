"""Typed models for Kilter API responses.

Parsing is deliberately tolerant: unknown fields are ignored and missing optional fields
get sensible defaults, because these endpoints are not a documented public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["flash", "send", "attempt"]


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return default


@dataclass(frozen=True)
class LogEntry:
    """One row from GET /api/logs.

    A row is a self-contained entry: one climb, at one angle, on one date, with the number of
    tries made in that entry. Repeats create new rows. ``topped``/``flashed`` define the
    outcome; ``difficulty_id`` is the climb's current consensus grade at that angle.
    """

    log_uuid: str
    climb_uuid: str
    climb_name: str
    angle: int | None
    attempts: int
    topped: bool
    flashed: bool
    created_at: datetime | None
    difficulty_id: int | None

    @property
    def status(self) -> Status:
        if self.topped and self.flashed:
            return "flash"
        if self.topped:
            return "send"
        return "attempt"

    @property
    def date(self) -> str | None:
        """Calendar date (UTC) as YYYY-MM-DD, used to group sessions."""
        return self.created_at.date().isoformat() if self.created_at else None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> LogEntry:
        topped = bool(raw.get("topped", False))
        flashed = bool(raw.get("flashed", False)) and topped
        attempts = _as_int(raw.get("attempts"), None)
        if attempts is None or attempts < 1:
            attempts = 1
        return cls(
            log_uuid=str(raw.get("logUuid") or ""),
            climb_uuid=str(raw.get("climbUuid") or ""),
            climb_name=str(raw.get("climbName") or "Unknown climb"),
            angle=_as_int(raw.get("angle")),
            attempts=attempts,
            topped=topped,
            flashed=flashed,
            created_at=parse_datetime(raw.get("createdAt")),
            difficulty_id=_as_int(raw.get("currentDifficultyId")),
        )


@dataclass(frozen=True)
class Grade:
    """One row from GET /api/grades (Kilter's 39-step difficulty scale)."""

    id: int
    boulder_label: str  # e.g. "6A/V3"
    font_scale: str  # e.g. "6A"
    v_scale: str  # e.g. "V3"
    is_listed: bool  # whether users can pick it in the app (ids 10-33)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Grade | None:
        grade_id = _as_int(raw.get("difficultyGradeId"))
        if grade_id is None:
            return None
        boulder = str(raw.get("boulderDifficulty") or "")
        font = str(raw.get("fontScale") or boulder.split("/")[0])
        v = str(raw.get("vScale") or (boulder.split("/")[1] if "/" in boulder else ""))
        return cls(
            id=grade_id,
            boulder_label=boulder or f"{font}/{v}",
            font_scale=font,
            v_scale=v,
            is_listed=bool(raw.get("isListed", False)),
        )
