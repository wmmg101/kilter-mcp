"""Pure catalogue functions: wall inference and candidate selection."""

from __future__ import annotations

from kilter_mcp.catalogue import candidates, stats_index, wall_profile
from kilter_mcp.models import Box, CatalogClimb, Catalogue, ClimbStat


def test_catalogue_parses_tolerantly(raw_curated_climbs, raw_logged_climbs):
    cat = Catalogue.from_api(raw_curated_climbs)
    assert len(cat.climbs) == 10
    assert len(cat.stats) == 13
    null_stat = next(s for s in cat.stats if s.difficulty_id is None)
    assert null_stat.quality_average is None and null_stat.ascent_count == 0
    sparse = next(c for c in Catalogue.from_api(raw_logged_climbs).climbs if c.name == "Sparse Row")
    assert sparse.box is None and sparse.is_listed is True and sparse.is_draft is False
    assert Catalogue.from_api(None) == Catalogue(climbs=[], stats=[])
    assert Catalogue.from_api({"climbs": [{"name": "no uuid"}], "climbStats": [{"angle": 1}]}) == (
        Catalogue(climbs=[], stats=[])
    )


def test_box_contains_and_union():
    wall = Box(8, 140, 4, 152)
    assert wall.contains(Box(20, 100, 8, 140))
    assert not wall.contains(Box(0, 100, 8, 140))  # sticks out left
    assert not wall.contains(Box(20, 100, 8, 160))  # sticks out top
    assert Box(20, 100, 8, 140).union(Box(8, 120, 4, 152)) == Box(8, 120, 4, 152)
    assert Box(20, 140, 8, 140).union(Box(8, 120, 4, 152)) == wall


def test_wall_profile_from_logged_climbs(logged_climbs):
    wall = wall_profile(logged_climbs)
    assert wall is not None
    # Dominant product wins; "Other Board" and the row without edges are ignored.
    assert wall.product_name == "Test Board"
    assert wall.layouts == frozenset({"10", "27"})
    assert wall.box == Box(left=8, right=140, bottom=4, top=152)
    assert wall.logged_climbs == 4
    assert wall.to_dict() == {
        "product_name": "Test Board",
        "layouts": ["10", "27"],
        "box": {"left": 8, "right": 140, "bottom": 4, "top": 152},
        "inferred_from_logged_climbs": 4,
    }


def test_wall_profile_needs_edges():
    assert wall_profile(Catalogue(climbs=[], stats=[])) is None
    no_edges = Catalogue(
        climbs=[CatalogClimb("u", "n", "Test Board", "10", None, "s", True, False)], stats=[]
    )
    assert wall_profile(no_edges) is None


def test_candidates_filter_fit_layout_product_draft_and_logged(logged_climbs, curated_climbs):
    wall = wall_profile(logged_climbs)
    assert wall is not None
    rows = candidates(curated_climbs, wall, angle=30, exclude={"test-climb-2"})
    names = [c.climb.name for c in rows]
    # quality desc, then ascents
    assert names == [
        "Curated Target",
        "Easy A",
        "Easy B",
        "Curated Hard",
        "Curated Target Low Quality",
    ]
    assert "Too Wide" not in names  # edge box exceeds the wall
    assert "Unknown Layout" not in names  # layout the user has never climbed
    assert "Other Product" not in names
    assert "Draft" not in names
    assert "Flash Climb" not in names  # excluded (already logged)
    assert all(c.stat.angle == 30 and c.difficulty_id is not None for c in rows)
    # at 40° only Easy A has stats
    assert [c.climb.name for c in candidates(curated_climbs, wall, angle=40)] == ["Easy A"]
    assert candidates(curated_climbs, wall, angle=30, min_ascents=400)[0].climb.name == "Easy A"


def test_stats_index_later_payload_wins(logged_climbs, curated_climbs):
    idx = stats_index(curated_climbs, logged_climbs)
    assert idx[("test-climb-1", 30)].ascent_count == 40
    assert isinstance(idx[("test-curated-target", 30)], ClimbStat)
    # the curated payload lists test-curated-target twice; the later (null) row wins
    assert idx[("test-curated-target", 30)].difficulty_id is None


def test_candidates_collapse_duplicate_curated_rows(logged_climbs):
    wall = wall_profile(logged_climbs)
    assert wall is not None
    climb = CatalogClimb(
        "dup", "Featured Twice", "Test Board", "10", Box(20, 100, 8, 140), "s", True, False
    )
    cat = Catalogue(
        climbs=[climb, climb],
        stats=[
            ClimbStat("dup", 30, 16, 50, 3.0),
            ClimbStat("dup", 30, 16, 50, 3.0),
            ClimbStat("dup", 30, None, 0, None),
        ],
    )
    rows = candidates(cat, wall, angle=30)
    assert [(c.climb.name, c.stat.ascent_count) for c in rows] == [("Featured Twice", 50)]


# -- repeats at a new angle ---------------------------------------------------------------------


def _stats_at_40(raw_climb_stats):
    return {
        (s.climb_uuid, s.angle): s
        for rows in raw_climb_stats.values()
        for s in (ClimbStat.from_api(r) for r in rows)
        if s
    }


def test_repeat_candidates_are_sent_elsewhere_graded_here(logs, logged_climbs, raw_climb_stats):
    from kilter_mcp.catalogue import repeat_candidates

    rows = repeat_candidates(logs, logged_climbs.climbs, _stats_at_40(raw_climb_stats), angle=40)
    names = [c.climb.name for c in rows]
    # Flash Climb was sent twice at 30°, Test Climb once: more sends ranks first.
    assert names == ["Flash Climb", "Test Climb"]
    # Steep Attempt is logged at 40° already; Project Climb only has a placeholder row at 40°;
    # Sparse Row has no stats at all.
    assert "Steep Attempt" not in names and "Project Climb" not in names
    flash = rows[0]
    assert flash.is_repeat and flash.difficulty_id == 15 and flash.stat.angle == 40
    assert [(p.angle, p.difficulty_id, p.flashed, p.sends) for p in flash.sent_at] == [
        (30, 13, True, 2)
    ]
    assert flash.climb.setter == "test-setter"  # row from the logged catalogue


def test_repeat_candidates_rank_by_my_rating_and_fall_back_to_log_name(grades, raw_climb_stats):
    from kilter_mcp.catalogue import repeat_candidates
    from kilter_mcp.models import LogEntry

    def send(uuid, name, angle, rating=None):
        raw = {
            "climbUuid": uuid,
            "climbName": name,
            "angle": angle,
            "topped": True,
            "createdAt": "2026-03-01T18:00:00Z",
            "currentDifficultyId": 13,
        }
        if rating:
            raw["climbRating"] = {"difficultyGradeId": 13, "rating": rating}
        return LogEntry.from_api(raw)

    logs = [send("test-climb-1", "Test Climb", 30), send("test-climb-2", "Flash Climb", 30, 5)]
    rows = repeat_candidates(logs, [], _stats_at_40(raw_climb_stats), angle=40)
    assert [c.climb.name for c in rows] == ["Flash Climb", "Test Climb"]
    assert rows[0].sent_at[0].my_rating == 5
    assert rows[0].climb.setter == ""  # synthesised from the log entry
    # A climb attempted but never sent elsewhere is not a repeat.
    attempt = LogEntry.from_api({"climbUuid": "test-climb-1", "angle": 30, "topped": False})
    assert repeat_candidates([attempt], [], _stats_at_40(raw_climb_stats), angle=40) == []
