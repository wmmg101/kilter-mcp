"""Pure functions over ``list[LogEntry]``. No I/O, no MCP.

Semantics (see .kiro/specs/kilter-mcp/requirements.md):
- one log row = one climb at one angle on one date, ``attempts`` tries in that entry
- ``topped``/``flashed`` define the outcome; a climb is identified by ``(climb_uuid, angle)``
- repeats create new rows, so counts below are "entries", and unique climbs are
  de-duplicated on ``(climb_uuid, angle)`` where it matters
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timezone, tzinfo
from typing import Any

from kilter_mcp.grades import GradeTable
from kilter_mcp.models import LogEntry

ClimbKey = tuple[str, int | None]
UTC = timezone.utc


# -- helpers ------------------------------------------------------------------------------------


def parse_date_arg(
    value: str | None, *, end_of_day: bool = False, tz: tzinfo = UTC
) -> datetime | None:
    """Parse YYYY-MM-DD (or ISO datetime) into an aware UTC datetime. None passes through.

    A bare date is interpreted as a calendar day in ``tz`` (the user's timezone), so
    ``start_date="2026-01-08"`` means midnight local time, not midnight UTC.
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            parsed = datetime(d.year, d.month, d.day, tzinfo=tz)
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999_999)
            return parsed.astimezone(UTC)
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; use YYYY-MM-DD.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def key_of(entry: LogEntry) -> ClimbKey:
    return (entry.climb_uuid, entry.angle)


def entry_to_dict(entry: LogEntry, grades: GradeTable, tz: tzinfo = UTC) -> dict[str, Any]:
    """Serialise one entry. ``date`` is the local timestamp in ``tz`` (ISO 8601 with offset)."""
    local = entry.created_at.astimezone(tz) if entry.created_at else None
    my_grade = grades.get(entry.user_difficulty_id)
    return {
        "climb_name": entry.climb_name,
        "date": local.isoformat() if local else None,
        "angle": entry.angle,
        "attempts": entry.attempts,
        "topped": entry.topped,
        "flashed": entry.flashed,
        "status": entry.status,
        **grades.describe(entry.difficulty_id),
        "my_difficulty_id": entry.user_difficulty_id,
        "my_grade": my_grade.v_scale if my_grade else None,
        "my_rating": entry.user_rating,
        "climb_uuid": entry.climb_uuid,
    }


def _max_difficulty(entries: Iterable[LogEntry]) -> int | None:
    ids = [e.difficulty_id for e in entries if e.difficulty_id is not None]
    return max(ids) if ids else None


def _sorted_desc(entries: Iterable[LogEntry]) -> list[LogEntry]:
    return sorted(
        entries,
        key=lambda e: e.created_at.timestamp() if e.created_at else 0.0,
        reverse=True,
    )


# -- filtering ----------------------------------------------------------------------------------


def filter_logs(
    logs: Iterable[LogEntry],
    *,
    angle: int | None = None,
    topped: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[LogEntry]:
    out: list[LogEntry] = []
    for e in logs:
        if angle is not None and e.angle != angle:
            continue
        if topped is not None and e.topped != topped:
            continue
        if start is not None and (e.created_at is None or e.created_at < start):
            continue
        if end is not None and (e.created_at is None or e.created_at > end):
            continue
        out.append(e)
    return _sorted_desc(out)


def sends(logs: Iterable[LogEntry]) -> list[LogEntry]:
    return _sorted_desc(e for e in logs if e.topped)


# -- projects -----------------------------------------------------------------------------------


def projects(
    logs: Iterable[LogEntry],
    grades: GradeTable,
    *,
    angle: int | None = None,
    tz: tzinfo = UTC,
) -> list[dict[str, Any]]:
    """Climbs (per ``(climb_uuid, angle)``) attempted but never topped at that angle."""
    topped_keys = {key_of(e) for e in logs if e.topped}
    groups: dict[ClimbKey, list[LogEntry]] = defaultdict(list)
    for e in logs:
        if e.topped or key_of(e) in topped_keys:
            continue
        if angle is not None and e.angle != angle:
            continue
        groups[key_of(e)].append(e)

    out: list[dict[str, Any]] = []
    for (climb_uuid, climb_angle), entries in groups.items():
        entries = _sorted_desc(entries)
        latest = entries[0]
        out.append(
            {
                "climb_name": latest.climb_name,
                "angle": climb_angle,
                "sessions": len({e.local_date(tz) for e in entries}),
                "total_attempts": sum(e.attempts for e in entries),
                "first_tried": entries[-1].local_date(tz),
                "last_tried": latest.local_date(tz),
                **grades.describe(latest.difficulty_id),
                "climb_uuid": climb_uuid,
            }
        )
    # Most recently tried first, then most attempts.
    out.sort(key=lambda p: (p["last_tried"] or "", p["total_attempts"]), reverse=True)
    return out


# -- summary ------------------------------------------------------------------------------------


def summary(logs: list[LogEntry], grades: GradeTable, *, tz: tzinfo = UTC) -> dict[str, Any]:
    sent = [e for e in logs if e.topped]
    flashes = [e for e in sent if e.flashed]
    days = sorted({d for d in (e.local_date(tz) for e in logs) if d})
    angles = sorted({e.angle for e in logs if e.angle is not None})
    hardest = _max_difficulty(sent)
    hardest_flash = _max_difficulty(flashes)
    return {
        "total_entries": len(logs),
        "sends": len(sent),
        "flashes": len(flashes),
        "attempt_entries_without_top": len(logs) - len(sent),
        "unique_climbs_sent": len({key_of(e) for e in sent}),
        "total_attempts_reported": sum(e.attempts for e in logs),
        "angles_climbed": angles,
        "first_entry": days[0] if days else None,
        "last_entry": days[-1] if days else None,
        "session_count": len(days),
        "hardest_send": grades.describe(hardest),
        "hardest_flash": grades.describe(hardest_flash),
        "sends_by_grade": _counts_by_grade(sent, grades),
        "grade_note": grades.grade_note(e.difficulty_id for e in sent),
        "grade_source": grades.source,
    }


def _counts_by_grade(entries: Iterable[LogEntry], grades: GradeTable) -> list[dict[str, Any]]:
    by_id: dict[int | None, int] = defaultdict(int)
    for e in entries:
        by_id[e.difficulty_id] += 1
    rows = [{**grades.describe(k), "count": v} for k, v in by_id.items()]
    rows.sort(key=lambda r: (r["difficulty_id"] is None, r["difficulty_id"] or 0))
    return rows


# -- grade pyramid ------------------------------------------------------------------------------


def grade_pyramid(
    logs: Iterable[LogEntry], grades: GradeTable, *, angle: int | None = None
) -> dict[str, Any]:
    """Sends per grade (unique climbs and entries), hardest first, with flash counts."""
    sent = [e for e in logs if e.topped and (angle is None or e.angle == angle)]
    by_id: dict[int | None, dict[str, Any]] = {}
    seen: set[tuple[int | None, ClimbKey]] = set()
    for e in sent:
        row = by_id.setdefault(
            e.difficulty_id, {"sends": 0, "unique_climbs": 0, "flashes": 0, "attempts": 0}
        )
        row["sends"] += 1
        row["attempts"] += e.attempts
        if e.flashed:
            row["flashes"] += 1
        if (e.difficulty_id, key_of(e)) not in seen:
            seen.add((e.difficulty_id, key_of(e)))
            row["unique_climbs"] += 1
    levels = [
        {
            **grades.describe(k),
            **v,
            "flash_rate": round(v["flashes"] / v["sends"], 3) if v["sends"] else 0.0,
        }
        for k, v in by_id.items()
    ]
    levels.sort(key=lambda r: (r["difficulty_id"] is None, -(r["difficulty_id"] or 0)))
    return {
        "angle": angle,
        "total_sends": len(sent),
        "levels": levels,
        "grade_note": grades.grade_note(e.difficulty_id for e in sent),
    }


# -- hardest sends ------------------------------------------------------------------------------


def hardest_sends(
    logs: Iterable[LogEntry],
    grades: GradeTable,
    *,
    limit: int = 10,
    angle: int | None = None,
    tz: tzinfo = UTC,
) -> list[dict[str, Any]]:
    """Unique sent climbs ordered by difficulty (desc), then most recent."""
    best: dict[ClimbKey, LogEntry] = {}
    for e in logs:
        if not e.topped or (angle is not None and e.angle != angle):
            continue
        k = key_of(e)
        current = best.get(k)
        if current is None or _later(e, current):
            best[k] = e
    ranked = sorted(
        best.values(),
        key=lambda e: (
            e.difficulty_id if e.difficulty_id is not None else -1,
            e.created_at.timestamp() if e.created_at else 0.0,
        ),
        reverse=True,
    )
    return [entry_to_dict(e, grades, tz) for e in ranked[: max(limit, 0)]]


def _later(a: LogEntry, b: LogEntry) -> bool:
    ta = a.created_at.timestamp() if a.created_at else 0.0
    tb = b.created_at.timestamp() if b.created_at else 0.0
    return ta > tb


# -- sessions -----------------------------------------------------------------------------------


def sessions(
    logs: Iterable[LogEntry], grades: GradeTable, *, limit: int = 10, tz: tzinfo = UTC
) -> list[dict[str, Any]]:
    """Group entries by calendar date in ``tz``; newest session first."""
    by_day: dict[str, list[LogEntry]] = defaultdict(list)
    for e in logs:
        day = e.local_date(tz)
        if day:
            by_day[day].append(e)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day, reverse=True)[: max(limit, 0)]:
        entries = _sorted_desc(by_day[day])
        sent = [e for e in entries if e.topped]
        out.append(
            {
                "date": day,
                "angles": sorted({e.angle for e in entries if e.angle is not None}),
                "entries": len(entries),
                "sends": len(sent),
                "flashes": sum(1 for e in sent if e.flashed),
                "attempts": sum(e.attempts for e in entries),
                "hardest_send": grades.describe(_max_difficulty(sent)),
                "climbs": [entry_to_dict(e, grades, tz) for e in entries],
            }
        )
    return out


# -- progression --------------------------------------------------------------------------------


def _period_key(dt: datetime, period: str) -> str:
    if period == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return f"{dt.year:04d}-{dt.month:02d}"


def progression(
    logs: Iterable[LogEntry],
    grades: GradeTable,
    *,
    period: str = "month",
    angle: int | None = None,
    tz: tzinfo = UTC,
) -> list[dict[str, Any]]:
    """Per month (or ISO week) in ``tz``: sends, flashes, sessions, hardest send. Oldest first."""
    if period not in ("month", "week"):
        raise ValueError("period must be 'month' or 'week'")
    buckets: dict[str, list[LogEntry]] = defaultdict(list)
    for e in logs:
        if e.created_at is None or (angle is not None and e.angle != angle):
            continue
        buckets[_period_key(e.created_at.astimezone(tz), period)].append(e)
    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        entries = buckets[key]
        sent = [e for e in entries if e.topped]
        out.append(
            {
                "period": key,
                "sessions": len({e.local_date(tz) for e in entries}),
                "entries": len(entries),
                "sends": len(sent),
                "unique_climbs_sent": len({key_of(e) for e in sent}),
                "flashes": sum(1 for e in sent if e.flashed),
                "attempts": sum(e.attempts for e in entries),
                "hardest_send": grades.describe(_max_difficulty(sent)),
                "hardest_flash": grades.describe(_max_difficulty(e for e in sent if e.flashed)),
            }
        )
    return out


# -- angle stats --------------------------------------------------------------------------------


def angle_stats(
    logs: Iterable[LogEntry], grades: GradeTable, *, tz: tzinfo = UTC
) -> list[dict[str, Any]]:
    """One row per wall angle so the agent can compare e.g. 20° vs 30°."""
    by_angle: dict[int | None, list[LogEntry]] = defaultdict(list)
    for e in logs:
        by_angle[e.angle].append(e)
    out: list[dict[str, Any]] = []
    for angle in sorted(by_angle, key=lambda a: (a is None, a or 0)):
        entries = by_angle[angle]
        sent = [e for e in entries if e.topped]
        flashes = [e for e in sent if e.flashed]
        out.append(
            {
                "angle": angle,
                "entries": len(entries),
                "sends": len(sent),
                "unique_climbs_sent": len({key_of(e) for e in sent}),
                "flashes": len(flashes),
                "flash_rate": round(len(flashes) / len(sent), 3) if sent else 0.0,
                "attempts": sum(e.attempts for e in entries),
                "sessions": len({d for d in (e.local_date(tz) for e in entries) if d}),
                "hardest_send": grades.describe(_max_difficulty(sent)),
                "hardest_flash": grades.describe(_max_difficulty(flashes)),
                "sends_by_grade": _counts_by_grade(sent, grades),
            }
        )
    return out
