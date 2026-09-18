from __future__ import annotations

from datetime import timezone

from kilter_mcp.grades import FALLBACK_TABLE, GradeTable
from kilter_mcp.models import Grade, LogEntry, parse_datetime


def test_log_entry_parses_full_row(raw_logs):
    e = LogEntry.from_api(raw_logs[0])
    assert e.climb_uuid == "test-climb-1"
    assert e.climb_name == "Test Climb"
    assert e.angle == 30
    assert e.attempts == 3
    assert e.topped is True
    assert e.flashed is False
    assert e.status == "send"
    assert e.difficulty_id == 10
    assert e.created_at is not None
    assert e.created_at.tzinfo == timezone.utc
    assert e.date == "2026-01-01"


def test_log_entry_status_values(logs):
    by_uuid = {e.log_uuid: e for e in logs}
    assert by_uuid["test-log-2"].status == "flash"
    assert by_uuid["test-log-1"].status == "send"
    assert by_uuid["test-log-3"].status == "attempt"


def test_log_entry_tolerates_missing_fields(raw_logs):
    sparse = LogEntry.from_api(raw_logs[-1])
    assert sparse.attempts == 1
    assert sparse.flashed is False
    assert sparse.difficulty_id is None
    assert sparse.topped is True


def test_flashed_requires_topped():
    e = LogEntry.from_api({"flashed": True, "topped": False})
    assert e.flashed is False
    assert e.status == "attempt"


def test_parse_datetime_variants():
    assert parse_datetime("2026-01-01T18:00:00Z").isoformat() == "2026-01-01T18:00:00+00:00"
    assert parse_datetime("2026-01-01T20:00:00+02:00").hour == 18
    assert parse_datetime("2026-01-01T18:00:00").tzinfo == timezone.utc
    assert parse_datetime("garbage") is None
    assert parse_datetime(None) is None


def test_grade_from_api(raw_grades):
    g = Grade.from_api(raw_grades[15])
    assert g == Grade(id=16, boulder_label="6A/V3", font_scale="6A", v_scale="V3", is_listed=True)


def test_fallback_table_matches_live_fixture(raw_grades):
    """The embedded fallback must stay in sync with the /api/grades fixture."""
    from_api = GradeTable.from_grades([g for g in map(Grade.from_api, raw_grades) if g])
    fallback = GradeTable.fallback()
    assert fallback.source == "fallback"
    assert set(fallback.grades) == set(from_api.grades)
    for gid, grade in from_api.grades.items():
        assert fallback.grades[gid] == grade
    assert len(FALLBACK_TABLE) == 39


def test_grade_table_lookups(grades: GradeTable):
    assert grades.v_grade(10) == "V0"
    assert grades.font_grade(12) == "4C"
    assert grades.label(22) == "7A/V6"
    assert grades.describe(18) == {
        "difficulty_id": 18,
        "grade": "V4",
        "font_grade": "6B",
        "label": "6B/V4",
    }
    assert grades.describe(None) == {
        "difficulty_id": None,
        "grade": None,
        "font_grade": None,
        "label": None,
    }
    assert grades.describe(999)["grade"] is None


def test_log_entry_parses_embedded_climb_rating():
    e = LogEntry.from_api(
        {
            "climbUuid": "test-climb-7",
            "topped": True,
            "createdAt": "2026-01-01T18:00:00Z",
            "currentDifficultyId": 16,
            "climbRating": {
                "climbRatingUuid": "test-rating",
                "difficultyGradeId": 17,
                "rating": 5,
                "comment": "great",
            },
        }
    )
    assert e.difficulty_id == 16
    assert e.user_difficulty_id == 17
    assert e.user_rating == 5


def test_log_entry_without_or_malformed_climb_rating():
    assert LogEntry.from_api({"topped": True}).user_difficulty_id is None
    assert LogEntry.from_api({"topped": True, "climbRating": None}).user_rating is None
    assert LogEntry.from_api({"topped": True, "climbRating": "n/a"}).user_rating is None


def test_local_date_respects_timezone():
    from zoneinfo import ZoneInfo

    e = LogEntry.from_api({"topped": True, "createdAt": "2026-06-12T00:15:00Z"})
    assert e.date == "2026-06-12"
    assert e.local_date(ZoneInfo("America/Denver")) == "2026-06-11"


def test_grade_note_when_v_scale_collapses(grades: GradeTable):
    # 10, 11, 12 are 4A, 4B, 4C: three Font grades, one V-grade.
    note = grades.grade_note([10, 11, 12, 11])
    assert note is not None
    assert "V0" in note
    assert "4A-4C" in note
    assert "font_grade" in note


def test_grade_note_absent_when_scales_agree(grades: GradeTable):
    # 13 (V1), 15 (V2), 16 (V3): one Font grade per V-grade, nothing to add.
    assert grades.grade_note([13, 15, 16]) is None
    assert grades.grade_note([]) is None
    assert grades.grade_note([None, 999]) is None


def test_grade_note_spans_multiple_v_grades(grades: GradeTable):
    # 16/17 are 6A/6A+ (both V3), 18/19 are 6B/6B+ (both V4).
    note = grades.grade_note([16, 17, 18, 19])
    assert note is not None and "V3/V4" in note and "6A-6B+" in note
