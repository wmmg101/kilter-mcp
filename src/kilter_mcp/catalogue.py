"""Pure functions over the climb catalogue (``Catalogue`` payloads). No I/O, no MCP.

The catalogue lets the session planner suggest climbs the user has not logged at the target
angle: *repeats* (climbs they sent at another angle, see ``repeat_candidates``) and climbs
*new* to them altogether (``candidates``). Two payloads feed it, both shaped
``{"climbs": [...], "climbStats": [...]}``:

- the user's *logged* climbs, used to learn what fits on their wall (see ``wall_profile``) and
  as the climb rows for repeats;
- Kilter's *curated* climbs, the pool for new climbs and a source of per-angle grades.

Kilter has no endpoint that says "these climbs fit wall X", and a wall accepts climbs from
several hold-set layouts, so fit is inferred conservatively: a candidate must be on the same
product, on a layout the user has already climbed, and its edge box must sit inside the box
spanned by the climbs the user has already logged. That can miss climbs that would fit; it
should never suggest one that does not.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from kilter_mcp.models import Box, CatalogClimb, Catalogue, ClimbStat, LogEntry

StatKey = tuple[str, int]


@dataclass(frozen=True)
class WallProfile:
    """What we can infer about the user's wall from the climbs they have logged."""

    product_name: str
    layouts: frozenset[str]
    box: Box
    logged_climbs: int

    def accepts(self, climb: CatalogClimb) -> bool:
        return (
            climb.product_name == self.product_name
            and climb.layout_uuid in self.layouts
            and climb.box is not None
            and self.box.contains(climb.box)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "layouts": sorted(self.layouts),
            "box": self.box.to_dict(),
            "inferred_from_logged_climbs": self.logged_climbs,
        }


def wall_profile(logged: Catalogue) -> WallProfile | None:
    """Infer the wall from logged climbs: dominant product, layouts seen, union of edge boxes.

    Returns None when nothing usable was logged (new account, or rows without edges).
    """
    with_box = [c for c in logged.climbs if c.box is not None and c.product_name]
    if not with_box:
        return None
    product = Counter(c.product_name for c in with_box).most_common(1)[0][0]
    same_product = [c for c in with_box if c.product_name == product]
    box = same_product[0].box
    assert box is not None
    for c in same_product[1:]:
        assert c.box is not None
        box = box.union(c.box)
    return WallProfile(
        product_name=product,
        layouts=frozenset(c.layout_uuid for c in same_product if c.layout_uuid),
        box=box,
        logged_climbs=len(same_product),
    )


def stats_index(*catalogues: Catalogue) -> dict[StatKey, ClimbStat]:
    """``(climb_uuid, angle) -> ClimbStat`` across payloads; later payloads win on conflict."""
    out: dict[StatKey, ClimbStat] = {}
    for cat in catalogues:
        for s in cat.stats:
            out[(s.climb_uuid, s.angle)] = s
    return out


@dataclass(frozen=True)
class PriorSend:
    """One angle at which the user has already sent a climb (for repeat suggestions)."""

    angle: int
    difficulty_id: int | None
    flashed: bool
    sends: int
    my_rating: int | None


@dataclass(frozen=True)
class Candidate:
    """A climb the user has not logged at the target angle, with community stats there.

    ``sent_at`` is non-empty for a *repeat*: a climb the user has sent at other angles and
    could try again at this one. Empty means a climb that is new to them altogether.
    """

    climb: CatalogClimb
    stat: ClimbStat
    sent_at: tuple[PriorSend, ...] = ()

    @property
    def difficulty_id(self) -> int | None:
        return self.stat.difficulty_id

    @property
    def is_repeat(self) -> bool:
        return bool(self.sent_at)


def repeat_candidates(
    logs: Iterable[LogEntry],
    climbs: Iterable[CatalogClimb],
    stats: dict[StatKey, ClimbStat],
    *,
    angle: int,
) -> list[Candidate]:
    """Climbs the user has sent at other angles, not logged at ``angle``, graded at ``angle``.

    ``stats`` must hold a ``(climb_uuid, angle)`` entry for a climb to qualify; callers gather
    those from the curated payload and per-climb stat lookups. Ranked by the user's own star
    rating (rated climbs are ones they want to do again), then times sent, then community
    quality.
    """
    by_uuid = {c.climb_uuid: c for c in climbs}
    names: dict[str, str] = {}
    logged_here: set[str] = set()
    sends: dict[str, dict[int, list[LogEntry]]] = defaultdict(lambda: defaultdict(list))
    for e in logs:
        if e.angle is None:
            continue
        if e.angle == angle:
            logged_here.add(e.climb_uuid)
        elif e.topped:
            sends[e.climb_uuid][e.angle].append(e)
            names.setdefault(e.climb_uuid, e.climb_name)
    out: list[Candidate] = []
    for uuid, per_angle in sends.items():
        if uuid in logged_here:
            continue
        stat = stats.get((uuid, angle))
        # Kilter returns placeholder rows (difficulty 1, zero ascents) for angles nobody has
        # climbed; a grade there means nothing, so require at least one ascent.
        if stat is None or stat.difficulty_id is None or stat.ascent_count < 1:
            continue
        # The climb row only adds the setter; a repeat fits the wall by definition, so a row
        # built from the log entry is enough when the catalogue has none.
        climb = by_uuid.get(uuid) or CatalogClimb(uuid, names[uuid], "", "", None, "", True, False)
        prior = tuple(
            PriorSend(
                angle=a,
                difficulty_id=entries[0].difficulty_id,
                flashed=any(e.flashed for e in entries),
                sends=len(entries),
                my_rating=max((e.user_rating for e in entries if e.user_rating), default=None),
            )
            for a, entries in sorted(per_angle.items())
        )
        out.append(Candidate(climb=climb, stat=stat, sent_at=prior))
    out.sort(
        key=lambda c: (
            max((p.my_rating or 0 for p in c.sent_at), default=0),
            sum(p.sends for p in c.sent_at),
            c.stat.quality_average or 0.0,
            c.climb.name,
        ),
        reverse=True,
    )
    return out


def candidates(
    curated: Catalogue,
    profile: WallProfile,
    *,
    angle: int,
    exclude: Iterable[str] = (),
    min_ascents: int = 1,
) -> list[Candidate]:
    """Curated climbs that fit the wall and have a grade at ``angle``, best first.

    ``exclude`` lists climb uuids to drop (typically everything already in the logbook at that
    angle). Ranking is quality, then popularity: a high ascent count also means the consensus
    grade is reliable.
    """
    excluded = set(exclude)
    by_uuid = {c.climb_uuid: c for c in curated.climbs}
    # The curated payload repeats a climb when it is featured more than once; keep one row per
    # climb, preferring a graded row with the most ascents.
    at_angle: dict[str, ClimbStat] = {}
    for stat in curated.stats:
        if stat.angle != angle:
            continue
        current = at_angle.get(stat.climb_uuid)
        if current is None or (stat.difficulty_id is not None, stat.ascent_count) > (
            current.difficulty_id is not None,
            current.ascent_count,
        ):
            at_angle[stat.climb_uuid] = stat
    out: list[Candidate] = []
    for stat in at_angle.values():
        if stat.difficulty_id is None or stat.ascent_count < min_ascents:
            continue
        if stat.climb_uuid in excluded:
            continue
        climb = by_uuid.get(stat.climb_uuid)
        if climb is None or climb.is_draft or not climb.is_listed or not profile.accepts(climb):
            continue
        out.append(Candidate(climb=climb, stat=stat))
    out.sort(
        key=lambda c: (c.stat.quality_average or 0.0, c.stat.ascent_count, c.climb.name),
        reverse=True,
    )
    return out
