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

from kilter_mcp.catalogue import Candidate
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


# -- session playlist ---------------------------------------------------------------------------

# Bands are in Kilter difficulty ids (one id = one Font step; a V-grade spans one or two).
# Relative to the target band [lo, hi]:
WARMUP_BAND = (-7, -2)  # 3C-5C for a 6B target: easy enough to move well, not trivial
MAIN_BAND = (-1, 1)  # the target plus a Font step either side
PROJECT_BAND = (2, 5)  # above the main band, but not hopeless


def _climb_history(entries: list[LogEntry], grades: GradeTable, tz: tzinfo) -> dict[str, Any]:
    """Collapse every entry for one ``(climb_uuid, angle)`` into one row for the playlist."""
    entries = _sorted_desc(entries)
    latest = entries[0]
    sends = sum(1 for e in entries if e.topped)
    flashes = sum(1 for e in entries if e.flashed)
    if flashes and sends == flashes:
        status = "flashed"
    elif sends:
        status = "sent"
    else:
        status = "project"
    return {
        "climb_name": latest.climb_name,
        "angle": latest.angle,
        **grades.describe(latest.difficulty_id),
        "status": status,
        "sends": sends,
        "flashes": flashes,
        "attempts": sum(e.attempts for e in entries),
        "sessions": len({d for d in (e.local_date(tz) for e in entries) if d}),
        "last_climbed": latest.local_date(tz),
        "climb_uuid": latest.climb_uuid,
    }


def _infer_target(logs: Iterable[LogEntry], angle: int) -> tuple[int | None, str | None]:
    """Hardest send at ``angle``; failing that, hardest send anywhere (with a note)."""
    at_angle = _max_difficulty(e for e in logs if e.topped and e.angle == angle)
    if at_angle is not None:
        return at_angle, f"Target inferred from your hardest send at {angle}°."
    anywhere = _max_difficulty(e for e in logs if e.topped)
    if anywhere is not None:
        return anywhere, (
            f"No sends logged at {angle}° yet; target inferred from your hardest send at any "
            "angle. The same climb is usually harder on a steeper wall."
        )
    return None, "No sends in the logbook, so no target grade could be inferred."


def _relative_grade(diff: int, grades: GradeTable, difficulty_id: int | None) -> str:
    label = grades.font_grade(difficulty_id) or "ungraded"
    if diff == 0:
        return f"{label}, at target"
    steps = abs(diff)
    unit = "Font step" if steps == 1 else "Font steps"
    return f"{label}, {steps} {unit} {'below' if diff < 0 else 'above'} target"


def _candidate_row(c: Candidate, grades: GradeTable) -> dict[str, Any]:
    """A climb not logged at this angle, in the same shape as a history row.

    ``status`` is ``"repeat"`` (sent at other angles, listed in ``sent_at``) or ``"new"``.
    The per-angle counters are zero because the user has no history *at this angle*.
    """
    return {
        "climb_name": c.climb.name,
        "angle": c.stat.angle,
        **grades.describe(c.difficulty_id),
        "status": "repeat" if c.is_repeat else "new",
        "sends": 0,
        "flashes": 0,
        "attempts": 0,
        "sessions": 0,
        "last_climbed": None,
        "climb_uuid": c.climb.climb_uuid,
        "source": "repeat" if c.is_repeat else "catalogue",
        "setter": c.climb.setter,
        "community_ascents": c.stat.ascent_count,
        "quality_average": c.stat.quality_average,
        "sent_at": [
            {
                "angle": p.angle,
                **grades.describe(p.difficulty_id),
                "flashed": p.flashed,
                "sends": p.sends,
                "my_rating": p.my_rating,
            }
            for p in c.sent_at
        ],
    }


def _repeat_reason(r: dict[str, Any]) -> str:
    parts = []
    for p in r["sent_at"]:
        if p["flashed"]:
            how = "flashed"
        elif p["sends"] == 1:
            how = "sent"
        else:
            how = f"sent {p['sends']} times"
        grade = f" ({p['font_grade']})" if p["font_grade"] else ""
        parts.append(f"{how} at {p['angle']}°{grade}")
    return (
        f"repeat at a new angle: {', '.join(parts)}; graded here from "
        f"{r['community_ascents']} community ascents"
    )


def session_playlist(
    logs: Iterable[LogEntry],
    grades: GradeTable,
    *,
    angle: int,
    target_ids: list[int] | None = None,
    warmups: int = 4,
    main: int = 6,
    projects: int = 2,
    new_climbs: Iterable[Candidate] = (),
    tz: tzinfo = UTC,
) -> dict[str, Any]:
    """Build an ordered climb list for an upcoming session at ``angle`` around a target grade.

    Climbs the user has already logged at that angle come first in every section; when a
    section is still short, it is topped up from ``new_climbs`` (catalogue climbs at this angle
    that fit the user's wall, best first, see ``catalogue.candidates``). ``target_ids`` is the
    id range of the requested grade (one id for a Font grade, one or two for a V-grade); when
    omitted the target is inferred from the hardest send at that angle. Sections:

    - ``warmup``: sent climbs 2-7 Font steps below target, easiest first; top-ups fill in
      ascending difficulty so the ramp stays a ramp.
    - ``main``: climbs within one Font step of target. Sent-but-not-flashed first (repeat to
      consolidate), then unsent ones at the grade, then flashes, then top-ups.
    - ``projects``: unsent climbs 2-5 Font steps above target, most attempts first, then
      top-ups.

    Within a top-up, *repeats* (climbs the user has sent at other angles, ``Candidate.sent_at``)
    come before climbs that are new to them. A climb appears in at most one section.
    """
    logs = list(logs)
    new_pool = [c for c in new_climbs if c.difficulty_id is not None and c.stat.angle == angle]
    notes: list[str] = []
    if target_ids:
        lo, hi = min(target_ids), max(target_ids)
        target_source = "requested"
    else:
        inferred, note = _infer_target(logs, angle)
        if note:
            notes.append(note)
        if inferred is None:
            return _empty_playlist(angle, grades, notes)
        lo = hi = inferred
        target_source = "inferred"

    groups: dict[ClimbKey, list[LogEntry]] = defaultdict(list)
    for e in logs:
        if e.angle == angle:
            groups[key_of(e)].append(e)
    rows = [
        {**_climb_history(entries, grades, tz), "source": "logbook"} for entries in groups.values()
    ]
    graded = [r for r in rows if r["difficulty_id"] is not None]
    if not rows and not new_pool:
        notes.append(
            f"Nothing logged at {angle}° yet and no catalogue climbs were available, so there "
            "is nothing to build a playlist from."
        )
    elif not rows:
        notes.append(
            f"Nothing logged at {angle}° yet; every suggestion is either a climb you have sent "
            "at another angle or one new to you from Kilter's curated list."
        )
    elif len(graded) < len(rows):
        notes.append(f"{len(rows) - len(graded)} climb(s) at {angle}° have no grade; skipped.")

    def in_band(d: int, band: tuple[int, int]) -> bool:
        # Offsets below target hang off the bottom of the target range, offsets above off the
        # top, so bands never overlap when the target spans two ids (V4 = 6B and 6B+).
        lower = (lo if band[0] < 0 else hi) + band[0]
        upper = (lo if band[1] < 0 else hi) + band[1]
        return lower <= d <= upper

    def offset(r: dict[str, Any]) -> int:
        d = r["difficulty_id"]
        return 0 if lo <= d <= hi else (d - lo if d < lo else d - hi)

    def fresh(
        band: tuple[int, int], count: int, *, ramp: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        """Best new climbs in ``band`` (and how many there were); ``ramp`` re-sorts the chosen
        few easiest-first."""
        pool = [c for c in new_pool if in_band(c.difficulty_id or 0, band)]
        pool.sort(key=lambda c: not c.is_repeat)  # stable: repeats first, then as ranked
        picked = pool[: max(count, 0)]
        if ramp:
            picked.sort(key=lambda c: (c.difficulty_id or 0, -(c.stat.quality_average or 0.0)))
        return [_candidate_row(c, grades) for c in picked], len(pool)

    warmup_pool = [
        r for r in graded if r["status"] != "project" and in_band(r["difficulty_id"], WARMUP_BAND)
    ]
    warmup_pool.sort(key=lambda r: (r["difficulty_id"], -r["flashes"], r["last_climbed"] or ""))

    main_rank = {"sent": 0, "project": 1, "flashed": 2}
    main_pool = [r for r in graded if in_band(r["difficulty_id"], MAIN_BAND)]
    main_pool.sort(
        key=lambda r: (main_rank[r["status"]], r["difficulty_id"], r["last_climbed"] or "")
    )

    project_pool = [
        r for r in graded if r["status"] == "project" and in_band(r["difficulty_id"], PROJECT_BAND)
    ]
    project_pool.sort(key=lambda r: (-r["attempts"], r["last_climbed"] or ""))

    def reason(r: dict[str, Any]) -> str:
        where = _relative_grade(offset(r), grades, r["difficulty_id"])
        if r["status"] == "repeat":
            return f"{where}; {_repeat_reason(r)}"
        if r["status"] == "new":
            quality = r["quality_average"]
            rated = f", rated {quality:.1f}" if quality is not None else ""
            return (
                f"{where}; new to you, from Kilter's curated list: "
                f"{r['community_ascents']} community ascents{rated}"
            )
        if r["status"] == "flashed":
            times = "once" if r["flashes"] == 1 else f"{r['flashes']} times"
            return f"{where}; flashed {times}, last on {r['last_climbed']}"
        if r["status"] == "sent":
            times = "once" if r["sends"] == 1 else f"{r['sends']} times"
            return f"{where}; sent {times}, last on {r['last_climbed']}"
        return (
            f"{where}; not yet topped after {r['attempts']} attempt(s) over "
            f"{r['sessions']} session(s), last tried {r['last_climbed']}"
        )

    def section(
        name: str,
        pool: list[dict[str, Any]],
        count: int,
        band: tuple[int, int],
        purpose: str,
        *,
        ramp: bool = False,
    ) -> dict[str, Any]:
        picked = pool[: max(count, 0)]
        extra, new_available = fresh(band, max(count, 0) - len(picked), ramp=ramp)
        return {
            "name": name,
            "purpose": purpose,
            "available": len(pool),
            "new_available": new_available,
            "climbs": [{**r, "reason": reason(r)} for r in picked + extra],
        }

    sections = [
        section(
            "warmup",
            warmup_pool,
            warmups,
            WARMUP_BAND,
            "Climbs well below target, easiest first: ones you have sent here, then repeats "
            "from other angles, then new ones.",
            ramp=True,
        ),
        section(
            "main",
            main_pool,
            main,
            MAIN_BAND,
            "Climbs at the target grade: ones to consolidate, unsent ones, then repeats from "
            "other angles, then new ones.",
        ),
        section(
            "projects",
            project_pool,
            projects,
            PROJECT_BAND,
            "Climbs above target: ones you have attempted here, then repeats from other "
            "angles, then new ones.",
        ),
    ]
    for s in sections:
        if s["available"] == 0 and s["new_available"] == 0 and (rows or new_pool):
            notes.append(f"No candidates for the '{s['name']}' section at {angle}°.")
    label_range = grades.label(lo) if lo == hi else f"{grades.label(lo)}-{grades.label(hi)}"
    return {
        "angle": angle,
        "target": {
            **grades.describe(lo),
            "difficulty_id_range": [lo, hi],
            "label_range": label_range,
            "source": target_source,
        },
        "logged_climbs_at_angle": len(rows),
        "new_climbs_considered": len(new_pool),
        "sections": sections,
        "total_climbs": sum(len(s["climbs"]) for s in sections),
        "notes": notes,
    }


def _empty_playlist(angle: int, grades: GradeTable, notes: list[str]) -> dict[str, Any]:
    return {
        "angle": angle,
        "target": {
            **grades.describe(None),
            "difficulty_id_range": None,
            "label_range": None,
            "source": "none",
        },
        "logged_climbs_at_angle": 0,
        "new_climbs_considered": 0,
        "sections": [],
        "total_climbs": 0,
        "notes": notes,
    }
