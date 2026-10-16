"""Typed models for Kilter API responses.

Parsing is deliberately tolerant: unknown fields are ignored and missing optional fields
get sensible defaults, because these endpoints are not a documented public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
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
    # The user's own opinion, from an embedded ``climbRating`` object when Kilter includes one.
    user_difficulty_id: int | None = None
    user_rating: int | None = None  # 1-5 stars

    @property
    def status(self) -> Status:
        if self.topped and self.flashed:
            return "flash"
        if self.topped:
            return "send"
        return "attempt"

    def local_date(self, tz: tzinfo = timezone.utc) -> str | None:
        """Calendar date (YYYY-MM-DD) in ``tz``; this is what defines a 'session'."""
        return self.created_at.astimezone(tz).date().isoformat() if self.created_at else None

    @property
    def date(self) -> str | None:
        """Calendar date in UTC. Prefer ``local_date(tz)`` for anything user-facing."""
        return self.local_date(timezone.utc)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> LogEntry:
        topped = bool(raw.get("topped", False))
        flashed = bool(raw.get("flashed", False)) and topped
        attempts = _as_int(raw.get("attempts"), None)
        if attempts is None or attempts < 1:
            attempts = 1
        rating = raw.get("climbRating")
        user_difficulty_id = user_rating = None
        if isinstance(rating, dict):
            user_difficulty_id = _as_int(rating.get("difficultyGradeId"))
            user_rating = _as_int(rating.get("rating"))
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
            user_difficulty_id=user_difficulty_id,
            user_rating=user_rating,
        )


@dataclass(frozen=True)
class Box:
    """Edge box on the board, in Kilter's hole coordinates (x grows right, y grows up)."""

    left: int
    right: int
    bottom: int
    top: int

    def contains(self, other: Box) -> bool:
        return (
            other.left >= self.left
            and other.right <= self.right
            and other.bottom >= self.bottom
            and other.top <= self.top
        )

    def union(self, other: Box) -> Box:
        return Box(
            left=min(self.left, other.left),
            right=max(self.right, other.right),
            bottom=min(self.bottom, other.bottom),
            top=max(self.top, other.top),
        )

    def to_dict(self) -> dict[str, int]:
        return {"left": self.left, "right": self.right, "bottom": self.bottom, "top": self.top}


@dataclass(frozen=True)
class CatalogClimb:
    """One climb from ``/api/climbs/logged`` or ``/api/climbs/curated``.

    ``layout_uuid`` is the hold-set variant the climb was set on (a small numeric string such as
    ``"10"``); climbs from several layouts are climbable on one wall, so it is used only to judge
    compatibility against layouts the user has already climbed. ``box`` is the climb's edge box.
    """

    climb_uuid: str
    name: str
    product_name: str
    layout_uuid: str
    box: Box | None
    setter: str
    is_listed: bool
    is_draft: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> CatalogClimb | None:
        uuid = str(raw.get("climbUuid") or "")
        if not uuid:
            return None
        edges = [_as_int(raw.get(k)) for k in ("edgeLeft", "edgeRight", "edgeBottom", "edgeTop")]
        box = None
        if all(e is not None for e in edges):
            box = Box(*edges)  # type: ignore[arg-type]
        return cls(
            climb_uuid=uuid,
            name=str(raw.get("name") or "Unknown climb"),
            product_name=str(raw.get("productName") or ""),
            layout_uuid=str(raw.get("productLayoutUuid") or ""),
            box=box,
            setter=str(raw.get("username") or ""),
            is_listed=bool(raw.get("isListed", True)),
            is_draft=bool(raw.get("isDraft", False)),
        )


@dataclass(frozen=True)
class ClimbStat:
    """Community stats for one climb at one angle (from the ``climbStats`` array)."""

    climb_uuid: str
    angle: int
    difficulty_id: int | None
    ascent_count: int
    quality_average: float | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ClimbStat | None:
        uuid = str(raw.get("climbUuid") or "")
        angle = _as_int(raw.get("angle"))
        if not uuid or angle is None:
            return None
        quality = raw.get("qualityAverage")
        return cls(
            climb_uuid=uuid,
            angle=angle,
            difficulty_id=_as_int(raw.get("currentDifficultyId")),
            ascent_count=_as_int(raw.get("ascentCount"), 0) or 0,
            quality_average=float(quality) if isinstance(quality, (int, float)) else None,
        )


@dataclass(frozen=True)
class Catalogue:
    """A climbs + stats payload, parsed."""

    climbs: list[CatalogClimb]
    stats: list[ClimbStat]

    @classmethod
    def from_api(cls, raw: Any) -> Catalogue:
        if not isinstance(raw, dict):
            return cls(climbs=[], stats=[])
        climbs = [
            c
            for c in (
                CatalogClimb.from_api(r) for r in raw.get("climbs") or [] if isinstance(r, dict)
            )
            if c
        ]
        stats = [
            s
            for s in (
                ClimbStat.from_api(r) for r in raw.get("climbStats") or [] if isinstance(r, dict)
            )
            if s
        ]
        return cls(climbs=climbs, stats=stats)


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
