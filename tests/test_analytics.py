from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kilter_mcp.analytics import (
    angle_stats,
    entry_to_dict,
    filter_logs,
    grade_pyramid,
    hardest_sends,
    parse_date_arg,
    progression,
    projects,
    sends,
    sessions,
    summary,
)


def test_parse_date_arg():
    assert parse_date_arg(None) is None
    assert parse_date_arg("") is None
    assert parse_date_arg("2026-01-08") == datetime(2026, 1, 8, tzinfo=timezone.utc)
    end = parse_date_arg("2026-01-08", end_of_day=True)
    assert end.hour == 23 and end.second == 59
    assert parse_date_arg("2026-01-08T10:00:00Z").hour == 10
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_date_arg("yesterday")


def test_filter_by_angle_topped_and_dates(logs):
    assert {e.angle for e in filter_logs(logs, angle=30)} == {30}
    assert all(e.topped for e in filter_logs(logs, topped=True))
    assert all(not e.topped for e in filter_logs(logs, topped=False))
    window = filter_logs(
        logs, start=parse_date_arg("2026-01-08"), end=parse_date_arg("2026-01-08", end_of_day=True)
    )
    assert sorted(e.log_uuid for e in window) == ["test-log-4", "test-log-5"]
    # newest first
    dates = [e.created_at for e in filter_logs(logs)]
    assert dates == sorted(dates, reverse=True)


def test_sends(logs):
    assert sorted(e.log_uuid for e in sends(logs)) == [
        "test-log-1",
        "test-log-2",
        "test-log-5",
        "test-log-7",
        "test-log-8",
        "test-log-9",
    ]


def test_entry_to_dict_shape(logs, grades):
    d = entry_to_dict(logs[0], grades)
    assert d == {
        "climb_name": "Test Climb",
        "date": "2026-01-01T18:00:00Z",
        "angle": 30,
        "attempts": 3,
        "topped": True,
        "flashed": False,
        "status": "send",
        "difficulty_id": 10,
        "grade": "V0",
        "font_grade": "4A",
        "climb_uuid": "test-climb-1",
    }
    # no other UUIDs leak into tool output
    assert not {"user_uuid", "gym_uuid", "wall_uuid", "log_uuid"} & set(d)


def test_projects_are_per_climb_and_angle(logs, grades):
    rows = projects(logs, grades)
    # climb-3 at 30° was tried twice and never topped there (topped at 20° doesn't count).
    # climb-4 at 40° was eventually topped, so it is not a project.
    assert len(rows) == 1
    p = rows[0]
    assert p["climb_name"] == "Project Climb"
    assert p["angle"] == 30
    assert p["sessions"] == 2
    assert p["total_attempts"] == 9
    assert p["first_tried"] == "2026-01-01"
    assert p["last_tried"] == "2026-01-08"
    assert p["grade"] == "V3"
    assert projects(logs, grades, angle=40) == []
    assert len(projects(logs, grades, angle=30)) == 1


def test_summary(logs, grades):
    s = summary(logs, grades)
    assert s["total_entries"] == 9
    assert s["sends"] == 6
    assert s["flashes"] == 2
    assert s["attempt_entries_without_top"] == 3
    assert s["unique_climbs_sent"] == 5  # climb-2 repeated
    assert s["total_attempts_reported"] == 3 + 1 + 5 + 4 + 2 + 2 + 6 + 1 + 1
    assert s["angles_climbed"] == [20, 30, 40]
    assert s["first_entry"] == "2026-01-01"
    assert s["last_entry"] == "2026-02-10"
    assert s["session_count"] == 4
    assert s["hardest_send"]["grade"] == "V4"
    assert s["hardest_flash"]["grade"] == "V1"
    assert s["grade_source"] == "api"
    by_grade = {r["difficulty_id"]: r["count"] for r in s["sends_by_grade"]}
    assert by_grade == {10: 1, 13: 2, 15: 1, 18: 1, None: 1}
    # None sorts last
    assert s["sends_by_grade"][-1]["difficulty_id"] is None


def test_grade_pyramid(logs, grades):
    p = grade_pyramid(logs, grades)
    assert p["total_sends"] == 6
    ids = [lvl["difficulty_id"] for lvl in p["levels"]]
    assert ids == [18, 15, 13, 10, None]  # hardest first, unknown last
    v1 = next(lvl for lvl in p["levels"] if lvl["difficulty_id"] == 13)
    assert v1 == {
        "difficulty_id": 13,
        "grade": "V1",
        "font_grade": "5A",
        "sends": 2,
        "unique_climbs": 1,
        "flashes": 2,
        "attempts": 2,
        "flash_rate": 1.0,
    }
    assert grade_pyramid(logs, grades, angle=40)["total_sends"] == 1


def test_hardest_sends_unique_and_ordered(logs, grades):
    rows = hardest_sends(logs, grades, limit=10)
    names = [(r["climb_name"], r["angle"]) for r in rows]
    assert names[0] == ("Steep Attempt", 40)
    assert names.count(("Flash Climb", 30)) == 1  # repeat collapsed
    assert rows[-1]["difficulty_id"] is None  # unknown grade sorts last
    assert len(hardest_sends(logs, grades, limit=2)) == 2
    assert hardest_sends(logs, grades, angle=20)[0]["climb_name"] == "Project Climb"


def test_sessions_grouped_by_day(logs, grades):
    s = sessions(logs, grades, limit=10)
    assert [x["date"] for x in s] == ["2026-02-10", "2026-02-03", "2026-01-08", "2026-01-01"]
    last = s[0]
    assert last["entries"] == 3
    assert last["sends"] == 3
    assert last["flashes"] == 1
    assert last["angles"] == [30, 40]
    assert last["hardest_send"]["grade"] == "V4"
    assert len(last["climbs"]) == 3
    assert len(sessions(logs, grades, limit=1)) == 1


def test_progression_by_month_and_week(logs, grades):
    months = progression(logs, grades)
    assert [m["period"] for m in months] == ["2026-01", "2026-02"]
    jan = months[0]
    assert jan["sessions"] == 2
    assert jan["sends"] == 3
    assert jan["flashes"] == 1
    assert jan["hardest_send"]["grade"] == "V2"
    feb = months[1]
    assert feb["unique_climbs_sent"] == 3
    weeks = progression(logs, grades, period="week")
    assert weeks[0]["period"].startswith("2026-W")
    assert progression(logs, grades, angle=40)[0]["period"] == "2026-02"
    with pytest.raises(ValueError, match="period"):
        progression(logs, grades, period="year")


def test_angle_stats(logs, grades):
    rows = angle_stats(logs, grades)
    assert [r["angle"] for r in rows] == [20, 30, 40]
    a30 = rows[1]
    assert a30["entries"] == 6
    assert a30["sends"] == 4
    assert a30["flashes"] == 2
    assert a30["flash_rate"] == 0.5
    assert a30["hardest_send"]["grade"] == "V1"
    a40 = rows[2]
    assert a40["sends"] == 1 and a40["flashes"] == 0 and a40["flash_rate"] == 0.0
    assert a40["hardest_send"]["grade"] == "V4"
