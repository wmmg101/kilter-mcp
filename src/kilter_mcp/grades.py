"""Difficulty-id → grade lookup.

Kilter grades climbs on a 39-step scale (inherited from the Aurora Climbing data model).
Log rows carry ``currentDifficultyId`` (1-39); the labels come from the public endpoint
``GET https://portal.kiltergrips.com/api/grades``. Ids 10-33 (V0-V16) are the ones users
can pick in the app (``isListed``); the rest exist but are unlisted.

``FALLBACK_TABLE`` is a verbatim copy of that endpoint's response captured on 2026-09-16 and
is used only if the endpoint is unreachable. If Kilter changes the scale, regenerate it from
the live endpoint (``tests/test_grades.py`` checks the fallback matches the fixture shape).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from kilter_mcp.models import Grade

# (difficulty id, Font scale, V scale, is_listed)
FALLBACK_TABLE: tuple[tuple[int, str, str, bool], ...] = (
    (1, "1A", "V0", False),
    (2, "1B", "V0", False),
    (3, "1C", "V0", False),
    (4, "2A", "V0", False),
    (5, "2B", "V0", False),
    (6, "2C", "V0", False),
    (7, "3A", "V0", False),
    (8, "3B", "V0", False),
    (9, "3C", "V0", False),
    (10, "4A", "V0", True),
    (11, "4B", "V0", True),
    (12, "4C", "V0", True),
    (13, "5A", "V1", True),
    (14, "5B", "V1", True),
    (15, "5C", "V2", True),
    (16, "6A", "V3", True),
    (17, "6A+", "V3", True),
    (18, "6B", "V4", True),
    (19, "6B+", "V4", True),
    (20, "6C", "V5", True),
    (21, "6C+", "V5", True),
    (22, "7A", "V6", True),
    (23, "7A+", "V7", True),
    (24, "7B", "V8", True),
    (25, "7B+", "V8", True),
    (26, "7C", "V9", True),
    (27, "7C+", "V10", True),
    (28, "8A", "V11", True),
    (29, "8A+", "V12", True),
    (30, "8B", "V13", True),
    (31, "8B+", "V14", True),
    (32, "8C", "V15", True),
    (33, "8C+", "V16", True),
    (34, "9A", "V17", False),
    (35, "9A+", "V18", False),
    (36, "9B", "V19", False),
    (37, "9B+", "V20", False),
    (38, "9C", "V21", False),
    (39, "9C+", "V22", False),
)


@dataclass(frozen=True)
class GradeTable:
    """Lookup from difficulty id to Grade. Source is 'api' or 'fallback'."""

    grades: dict[int, Grade]
    source: str = "api"
    _order: tuple[int, ...] = field(default=(), repr=False)

    @classmethod
    def from_grades(cls, grades: list[Grade], source: str = "api") -> GradeTable:
        by_id = {g.id: g for g in grades}
        return cls(grades=by_id, source=source, _order=tuple(sorted(by_id)))

    @classmethod
    def fallback(cls) -> GradeTable:
        grades = [
            Grade(id=i, boulder_label=f"{font}/{v}", font_scale=font, v_scale=v, is_listed=listed)
            for i, font, v, listed in FALLBACK_TABLE
        ]
        return cls.from_grades(grades, source="fallback")

    def get(self, difficulty_id: int | None) -> Grade | None:
        if difficulty_id is None:
            return None
        return self.grades.get(difficulty_id)

    def v_grade(self, difficulty_id: int | None) -> str | None:
        grade = self.get(difficulty_id)
        return grade.v_scale if grade else None

    def font_grade(self, difficulty_id: int | None) -> str | None:
        grade = self.get(difficulty_id)
        return grade.font_scale if grade else None

    def label(self, difficulty_id: int | None) -> str | None:
        grade = self.get(difficulty_id)
        return grade.boulder_label if grade else None

    def describe(self, difficulty_id: int | None) -> dict[str, object]:
        """Fields merged into tool output for a difficulty id.

        ``grade`` is the V-scale, ``font_grade`` the Font scale and ``label`` both together
        (e.g. ``"4C/V0"``). The V-scale lumps Kilter's ids 1-12 into V0 and pairs several
        higher ids too, so Font is the finer-grained one; both are always present.
        """
        grade = self.get(difficulty_id)
        return {
            "difficulty_id": difficulty_id,
            "grade": grade.v_scale if grade else None,
            "font_grade": grade.font_scale if grade else None,
            "label": grade.boulder_label if grade else None,
        }

    def grade_note(self, difficulty_ids: Iterable[int | None]) -> str | None:
        """Explain when the V-scale hides real differences in a set of sends.

        Returns a sentence when the ids span more Font grades than V-grades (e.g. everything
        is "V0" but ranges 4A-4C), otherwise None. Meant to be included verbatim in tool
        output so the agent reports the finer scale.
        """
        found = [g for g in (self.get(i) for i in difficulty_ids) if g]
        if not found:
            return None
        v_grades = {g.v_scale for g in found}
        font_grades = sorted({g.font_scale for g in found}, key=lambda f: self._font_order(f))
        if len(font_grades) <= len(v_grades):
            return None
        v_span = "/".join(sorted(v_grades, key=lambda v: int(v[1:]) if v[1:].isdigit() else 0))
        return (
            f"V-scale collapses these sends into {v_span}; use font_grade "
            f"({font_grades[0]}-{font_grades[-1]}) to distinguish levels."
        )

    def _font_order(self, font: str) -> int:
        for gid, grade in self.grades.items():
            if grade.font_scale == font:
                return gid
        return 0
