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
        "date": "2026-01-01T18:00:00+00:00",
        "angle": 30,
        "attempts": 3,
        "topped": True,
        "flashed": False,
        "status": "send",
        "difficulty_id": 10,
        "grade": "V0",
        "font_grade": "4A",
        "label": "4A/V0",
        "my_difficulty_id": None,
        "my_grade": None,
        "my_rating": None,
        "climb_uuid": "test-climb-1",
    }
    # no other UUIDs leak into tool output
    assert not {"user_uuid", "gym_uuid", "wall_uuid", "log_uuid"} & set(d)


def test_entry_to_dict_local_time_and_my_grade(grades):
    from zoneinfo import ZoneInfo

    from kilter_mcp.models import LogEntry

    e = LogEntry.from_api(
        {
            "climbUuid": "test-climb-9",
            "climbName": "Rated",
            "angle": 40,
            "topped": True,
            "attempts": 2,
            "createdAt": "2026-03-01T02:30:00Z",
            "currentDifficultyId": 18,
            "climbRating": {"difficultyGradeId": 19, "rating": 4},
        }
    )
    d = entry_to_dict(e, grades, ZoneInfo("Europe/Rome"))
    assert d["date"] == "2026-03-01T03:30:00+01:00"
    assert d["grade"] == "V4"
    assert (d["my_difficulty_id"], d["my_grade"], d["my_rating"]) == (19, "V4", 4)


# -- timezone handling --------------------------------------------------------------------------


def _late_night_logs():
    """Two climbs 30 minutes apart straddling UTC midnight; one evening in Denver."""
    from kilter_mcp.models import LogEntry

    return [
        LogEntry.from_api(
            {
                "climbUuid": "c1",
                "climbName": "Before midnight UTC",
                "angle": 30,
                "topped": True,
                "createdAt": "2026-06-11T23:45:00Z",  # 17:45 Denver, 10 Jun
                "currentDifficultyId": 13,
            }
        ),
        LogEntry.from_api(
            {
                "climbUuid": "c2",
                "climbName": "After midnight UTC",
                "angle": 30,
                "topped": True,
                "createdAt": "2026-06-12T00:15:00Z",  # 18:15 Denver, 10 Jun
                "currentDifficultyId": 14,
            }
        ),
    ]


def test_sessions_split_in_utc_but_not_in_local_timezone(grades):
    from zoneinfo import ZoneInfo

    logs = _late_night_logs()
    assert len(sessions(logs, grades)) == 2  # naive UTC grouping splits the evening
    denver = sessions(logs, grades, tz=ZoneInfo("America/Denver"))
    assert len(denver) == 1
    assert denver[0]["date"] == "2026-06-11"
    assert denver[0]["sends"] == 2
    assert denver[0]["climbs"][0]["date"].endswith("-06:00")


def test_summary_progression_projects_use_timezone(grades):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Denver")
    logs = _late_night_logs()
    assert summary(logs, grades)["session_count"] == 2
    s = summary(logs, grades, tz=tz)
    assert s["session_count"] == 1
    assert s["first_entry"] == s["last_entry"] == "2026-06-11"
    # progression by week: 2026-06-11 (Thu) and 2026-06-12 (Fri) are the same ISO week
    # either way, so use a month boundary instead.
    from kilter_mcp.models import LogEntry

    eom = [
        LogEntry.from_api(
            {
                "climbUuid": "p",
                "climbName": "Month edge",
                "angle": 30,
                "topped": False,
                "createdAt": "2026-07-01T02:00:00Z",  # 30 Jun 20:00 Denver
            }
        )
    ]
    assert progression(eom, grades)[0]["period"] == "2026-07"
    assert progression(eom, grades, tz=tz)[0]["period"] == "2026-06"
    assert projects(eom, grades, tz=tz)[0]["last_tried"] == "2026-06-30"
    assert angle_stats(eom, grades, tz=tz)[0]["sessions"] == 1


def test_parse_date_arg_uses_timezone():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Denver")
    start = parse_date_arg("2026-06-11", tz=tz)
    assert start == datetime(2026, 6, 11, 6, 0, tzinfo=timezone.utc)  # 00:00 MDT
    end = parse_date_arg("2026-06-11", end_of_day=True, tz=tz)
    assert end.hour == 5 and end.day == 12  # 23:59:59 MDT is 05:59:59Z next day
    logs = _late_night_logs()
    assert len(filter_logs(logs, start=start, end=end)) == 2


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
        "label": "5A/V1",
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


def test_summary_and_pyramid_carry_grade_note_for_v0_logbook(grades):
    from kilter_mcp.models import LogEntry

    logs = [
        LogEntry.from_api(
            {
                "climbUuid": f"c{i}",
                "angle": 20,
                "topped": True,
                "createdAt": "2026-09-15T17:00:00Z",
                "currentDifficultyId": gid,
            }
        )
        for i, gid in enumerate([10, 11, 11, 12])
    ]
    assert "4A-4C" in summary(logs, grades)["grade_note"]
    assert "4A-4C" in grade_pyramid(logs, grades)["grade_note"]


def test_no_grade_note_when_font_and_v_scales_align(logs, grades):
    # Fixture sends are ids 10, 13, 15, 18 (+ one unknown): one Font grade per V-grade.
    assert summary(logs, grades)["grade_note"] is None
    assert grade_pyramid(logs, grades)["grade_note"] is None


# -- session playlist ---------------------------------------------------------------------------


def _names(section):
    return [c["climb_name"] for c in section["climbs"]]


def _section(plan, name):
    return next(s for s in plan["sections"] if s["name"] == name)


def test_session_playlist_infers_target_from_hardest_send_at_angle(logs, grades):
    from kilter_mcp.analytics import session_playlist

    plan = session_playlist(logs, grades, angle=30)
    # Hardest send at 30° is Flash Climb (id 13 / 5A). Bands relative to 13:
    # warm-up 6-11 → Test Climb (10); main 12-14 → Flash Climb; projects 15-18 → Project Climb.
    assert plan["angle"] == 30
    assert plan["target"]["source"] == "inferred"
    assert plan["target"]["difficulty_id"] == 13
    assert plan["target"]["difficulty_id_range"] == [13, 13]
    assert plan["target"]["label_range"] == "5A/V1"
    assert plan["logged_climbs_at_angle"] == 4
    assert [s["name"] for s in plan["sections"]] == ["warmup", "main", "projects"]
    assert _names(_section(plan, "warmup")) == ["Test Climb"]
    assert _names(_section(plan, "main")) == ["Flash Climb"]
    assert _names(_section(plan, "projects")) == ["Project Climb"]
    assert plan["total_climbs"] == 3
    assert any("inferred from your hardest send at 30°" in n for n in plan["notes"])
    assert any("no grade; skipped" in n for n in plan["notes"])  # Sparse Row


def test_session_playlist_climb_rows_carry_history_and_reason(logs, grades):
    from kilter_mcp.analytics import session_playlist

    plan = session_playlist(logs, grades, angle=30)
    warm = _section(plan, "warmup")["climbs"][0]
    assert warm == {
        "climb_name": "Test Climb",
        "angle": 30,
        "difficulty_id": 10,
        "grade": "V0",
        "font_grade": "4A",
        "label": "4A/V0",
        "status": "sent",
        "sends": 1,
        "flashes": 0,
        "attempts": 3,
        "sessions": 1,
        "last_climbed": "2026-01-01",
        "climb_uuid": "test-climb-1",
        "source": "logbook",
        "reason": "4A, 3 Font steps below target; sent once, last on 2026-01-01",
    }
    main = _section(plan, "main")["climbs"][0]
    assert main["status"] == "flashed" and main["flashes"] == 2 and main["sends"] == 2
    assert main["reason"] == "5A, at target; flashed 2 times, last on 2026-02-10"
    proj = _section(plan, "projects")["climbs"][0]
    assert proj["status"] == "project"
    assert proj["attempts"] == 9 and proj["sessions"] == 2
    assert proj["reason"].startswith("6A, 3 Font steps above target; not yet topped after 9")
    # no other UUIDs leak
    assert not {"user_uuid", "gym_uuid", "wall_uuid", "log_uuid"} & set(proj)


def test_session_playlist_with_requested_v_grade_uses_id_range(logs, grades):
    from kilter_mcp.analytics import session_playlist

    # V3 is ids 16 and 17 → target band [16, 17]; warm-up 9-14, main 15-18, projects 19-22.
    plan = session_playlist(logs, grades, angle=30, target_ids=[16, 17])
    assert plan["target"]["source"] == "requested"
    assert plan["target"]["difficulty_id_range"] == [16, 17]
    assert plan["target"]["label_range"] == "6A/V3-6A+/V3"
    assert _names(_section(plan, "warmup")) == ["Test Climb", "Flash Climb"]  # easiest first
    assert _names(_section(plan, "main")) == ["Project Climb"]
    assert _section(plan, "projects")["climbs"] == []
    assert _section(plan, "projects")["available"] == 0
    assert any("'projects' section" in n for n in plan["notes"])
    assert not any("inferred" in n for n in plan["notes"])


def test_session_playlist_respects_counts_and_reports_available(logs, grades):
    from kilter_mcp.analytics import session_playlist

    plan = session_playlist(logs, grades, angle=30, target_ids=[16, 17], warmups=1)
    warm = _section(plan, "warmup")
    assert warm["available"] == 2
    assert _names(warm) == ["Test Climb"]
    plan = session_playlist(logs, grades, angle=30, target_ids=[16], warmups=0, main=0)
    assert plan["total_climbs"] == 0


def test_session_playlist_eventually_sent_climb_is_not_a_project(logs, grades):
    from kilter_mcp.analytics import session_playlist

    # Steep Attempt at 40° was attempted then sent; it belongs in main, not projects.
    plan = session_playlist(logs, grades, angle=40)
    assert plan["target"]["difficulty_id"] == 18
    main = _section(plan, "main")["climbs"]
    assert [(c["climb_name"], c["status"], c["sends"], c["attempts"]) for c in main] == [
        ("Steep Attempt", "sent", 1, 8)
    ]
    assert _section(plan, "projects")["climbs"] == []


def test_session_playlist_main_section_ordering(grades):
    from kilter_mcp.analytics import session_playlist
    from kilter_mcp.models import LogEntry

    def row(uuid, name, gid, *, topped, flashed=False, when="2026-05-01T18:00:00Z"):
        return LogEntry.from_api(
            {
                "climbUuid": uuid,
                "climbName": name,
                "angle": 30,
                "topped": topped,
                "flashed": flashed,
                "createdAt": when,
                "currentDifficultyId": gid,
            }
        )

    logs = [
        row("f", "Flashed at target", 18, topped=True, flashed=True),
        row("p", "Unsent at target", 18, topped=False),
        row("s2", "Sent harder", 19, topped=True),
        row("s1", "Sent easier", 17, topped=True),
        row("x", "Too hard for main", 20, topped=False),
    ]
    plan = session_playlist(logs, grades, angle=30, target_ids=[18])
    # sent (easiest first) → unsent → flashed
    assert _names(_section(plan, "main")) == [
        "Sent easier",
        "Sent harder",
        "Unsent at target",
        "Flashed at target",
    ]
    assert _names(_section(plan, "projects")) == ["Too hard for main"]


def test_session_playlist_angle_without_history(logs, grades):
    from kilter_mcp.analytics import session_playlist

    plan = session_playlist(logs, grades, angle=35, target_ids=[18])
    assert plan["logged_climbs_at_angle"] == 0
    assert all(s["climbs"] == [] for s in plan["sections"])
    assert any("Nothing logged at 35°" in n for n in plan["notes"])
    # per-section "no candidates" notes are pointless when there is no history at all
    assert not any("section" in n for n in plan["notes"])

    inferred = session_playlist(logs, grades, angle=35)
    assert inferred["target"]["difficulty_id"] == 18  # hardest send anywhere
    assert any("any angle" in n for n in inferred["notes"])


def test_session_playlist_empty_logbook(grades):
    from kilter_mcp.analytics import session_playlist

    plan = session_playlist([], grades, angle=30)
    assert plan["sections"] == []
    assert plan["total_climbs"] == 0
    assert plan["target"]["source"] == "none"
    assert plan["target"]["difficulty_id"] is None
    assert any("No sends in the logbook" in n for n in plan["notes"])


# -- session playlist + catalogue ---------------------------------------------------------------


def _new_climbs(logged_climbs, curated_climbs, logs, angle):
    from kilter_mcp.catalogue import candidates, wall_profile

    wall = wall_profile(logged_climbs)
    return candidates(curated_climbs, wall, angle=angle, exclude={e.climb_uuid for e in logs})


def test_session_playlist_tops_up_sections_with_new_climbs(
    logs, grades, logged_climbs, curated_climbs
):
    from kilter_mcp.analytics import session_playlist

    fresh = _new_climbs(logged_climbs, curated_climbs, logs, 30)
    plan = session_playlist(logs, grades, angle=30, new_climbs=fresh)
    assert plan["new_climbs_considered"] == 5
    # Target inferred as 13. Warm-up band 6-11: logbook Test Climb (10) first, then the new
    # climbs in ascending difficulty (Easy B 10, Easy A 11) so the ramp still ramps.
    warm = _section(plan, "warmup")
    assert _names(warm) == ["Test Climb", "Easy B", "Easy A"]
    assert [c["source"] for c in warm["climbs"]] == ["logbook", "catalogue", "catalogue"]
    assert warm["available"] == 1 and warm["new_available"] == 2
    # Main band 12-14: only the logbook flash; nothing new at that grade.
    assert _names(_section(plan, "main")) == ["Flash Climb"]
    # Projects band 15-18: the logbook project first, then the best-rated new climb at 16.
    proj = _section(plan, "projects")
    assert _names(proj) == ["Project Climb", "Curated Target"]
    assert proj["new_available"] == 2
    new_row = proj["climbs"][1]
    assert new_row["status"] == "new"
    assert new_row["source"] == "catalogue"
    assert new_row["setter"] == "setter-a"
    assert new_row["community_ascents"] == 120
    assert new_row["quality_average"] == 3.0
    assert new_row["sends"] == 0 and new_row["last_climbed"] is None
    assert new_row["reason"] == (
        "6A, 3 Font steps above target; new to you, from Kilter's curated list: "
        "120 community ascents, rated 3.0"
    )
    assert not any("No candidates" in n for n in plan["notes"])


def test_session_playlist_new_climbs_respect_counts_and_band(
    logs, grades, logged_climbs, curated_climbs
):
    from kilter_mcp.analytics import session_playlist

    fresh = _new_climbs(logged_climbs, curated_climbs, logs, 30)
    plan = session_playlist(logs, grades, angle=30, target_ids=[16, 17], new_climbs=fresh, main=2)
    # Main band 15-18 with main=2: logbook project fills one slot, one new climb (best quality).
    assert _names(_section(plan, "main")) == ["Project Climb", "Curated Target"]
    # Projects band 19-22: no logbook project there, so the new 6B+ fills in.
    assert _names(_section(plan, "projects")) == ["Curated Hard"]
    # Counts of zero mean no top-up either.
    plan = session_playlist(logs, grades, angle=30, target_ids=[16], new_climbs=fresh, warmups=0)
    assert _section(plan, "warmup")["climbs"] == []


def test_session_playlist_at_unlogged_angle_uses_only_new_climbs(
    logs, grades, logged_climbs, curated_climbs
):
    from kilter_mcp.analytics import session_playlist

    fresh = _new_climbs(logged_climbs, curated_climbs, logs, 40)  # only Easy A (13) at 40°
    plan = session_playlist(logs, grades, angle=40, target_ids=[18], new_climbs=fresh)
    assert plan["logged_climbs_at_angle"] == 1  # Steep Attempt is logged at 40°
    plan = session_playlist(logs, grades, angle=40, target_ids=[18], new_climbs=fresh, warmups=1)
    assert _names(_section(plan, "warmup")) == ["Easy A"]
    # a different angle's candidates are ignored even if passed in
    wrong = _new_climbs(logged_climbs, curated_climbs, logs, 30)
    plan = session_playlist(logs, grades, angle=35, target_ids=[18], new_climbs=wrong)
    assert plan["new_climbs_considered"] == 0
    assert any("no catalogue climbs were available" in n for n in plan["notes"])


def test_session_playlist_note_when_everything_is_new(grades, logged_climbs, curated_climbs):
    from kilter_mcp.analytics import session_playlist

    fresh = _new_climbs(logged_climbs, curated_climbs, [], 30)
    plan = session_playlist([], grades, angle=30, target_ids=[16], new_climbs=fresh)
    assert plan["total_climbs"] > 0
    assert all(c["status"] == "new" for s in plan["sections"] for c in s["climbs"])
    assert any("every suggestion is either" in n for n in plan["notes"])


def test_session_playlist_bands_do_not_overlap_for_two_id_targets(grades):
    from kilter_mcp.analytics import session_playlist
    from kilter_mcp.models import LogEntry

    def row(uuid, gid):
        return LogEntry.from_api(
            {
                "climbUuid": uuid,
                "climbName": f"id{gid}",
                "angle": 30,
                "topped": False,
                "createdAt": "2026-05-01T18:00:00Z",
                "currentDifficultyId": gid,
            }
        )

    # V4 = ids 18-19. Main must be 17-20, projects 21-24; id 20 was previously in both.
    logs = [row(f"c{g}", g) for g in range(15, 26)]
    plan = session_playlist(logs, grades, angle=30, target_ids=[18, 19], main=50, projects=50)
    main_ids = sorted(c["difficulty_id"] for c in _section(plan, "main")["climbs"])
    proj_ids = sorted(c["difficulty_id"] for c in _section(plan, "projects")["climbs"])
    assert main_ids == [17, 18, 19, 20]
    assert proj_ids == [21, 22, 23, 24]
    assert not set(main_ids) & set(proj_ids)


def test_session_playlist_prefers_repeats_over_new_climbs(
    logs, grades, logged_climbs, curated_climbs, raw_climb_stats
):
    from kilter_mcp.analytics import session_playlist
    from kilter_mcp.catalogue import candidates, repeat_candidates, stats_index, wall_profile
    from kilter_mcp.models import ClimbStat

    stats = stats_index(curated_climbs, logged_climbs)
    for rows in raw_climb_stats.values():
        for raw in rows:
            s = ClimbStat.from_api(raw)
            assert s is not None
            stats.setdefault((s.climb_uuid, s.angle), s)
    repeats = repeat_candidates(logs, logged_climbs.climbs, stats, angle=40)
    wall = wall_profile(logged_climbs)
    fresh = candidates(curated_climbs, wall, angle=40, exclude={e.climb_uuid for e in logs})
    assert [c.climb.name for c in repeats] == ["Flash Climb", "Test Climb"]
    assert [c.climb.name for c in fresh] == ["Easy A"]

    # Target inferred at 40° is 18; warm-up band 11-16 holds both repeats (12, 15) and Easy A
    # (13). With room for two, repeats win, then the ramp sorts them easiest first.
    plan = session_playlist(logs, grades, angle=40, new_climbs=repeats + fresh, warmups=2)
    warm = _section(plan, "warmup")
    assert _names(warm) == ["Test Climb", "Flash Climb"]
    assert [c["status"] for c in warm["climbs"]] == ["repeat", "repeat"]
    assert [c["source"] for c in warm["climbs"]] == ["repeat", "repeat"]
    assert warm["new_available"] == 3
    row = warm["climbs"][1]
    assert row["sent_at"] == [
        {
            "angle": 30,
            "difficulty_id": 13,
            "grade": "V1",
            "font_grade": "5A",
            "label": "5A/V1",
            "flashed": True,
            "sends": 2,
            "my_rating": None,
        }
    ]
    assert row["reason"] == (
        "5C, 3 Font steps below target; repeat at a new angle: flashed at 30° (5A); "
        "graded here from 60 community ascents"
    )
    # With room for all three the ramp interleaves by difficulty.
    plan = session_playlist(logs, grades, angle=40, new_climbs=repeats + fresh, warmups=4)
    assert _names(_section(plan, "warmup")) == ["Test Climb", "Easy A", "Flash Climb"]
