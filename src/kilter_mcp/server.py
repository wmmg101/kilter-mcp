"""MCP server exposing read-only Kilter Board logbook tools.

Kiro launches this over stdio. Tools are thin: parse arguments, fetch via ``KilterClient``,
run pure analytics, return structured data. No OAuth or raw HTTP lives here.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from collections.abc import Callable
from datetime import tzinfo
from typing import Annotated, Any, Literal, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kilter_mcp import __version__
from kilter_mcp.analytics import (
    angle_stats,
    entry_to_dict,
    filter_logs,
    grade_pyramid,
    hardest_sends,
    parse_date_arg,
    progression,
    projects,
    session_playlist,
    sessions,
    summary,
)
from kilter_mcp.auth import AuthError
from kilter_mcp.catalogue import (
    Candidate,
    WallProfile,
    candidates,
    repeat_candidates,
    stats_index,
    wall_profile,
)
from kilter_mcp.client import KilterAPIError, KilterClient, Logbook
from kilter_mcp.config import ConfigError, Settings, resolve_timezone, timezone_name
from kilter_mcp.grades import GradeTable
from kilter_mcp.models import Catalogue, ClimbStat, LogEntry

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

_F = TypeVar("_F", bound=Callable[..., Any])

# Parameter types shared across tools. The Field descriptions land in each tool's JSON schema,
# which is what the agent reads when deciding how to call it.
Angle = Annotated[
    int | None,
    Field(description="Wall angle in degrees (e.g. 20, 30, 40). Omit for all angles."),
]
Limit = Annotated[int | None, Field(description="Maximum number of items to return.", ge=1)]
Topped = Annotated[
    bool | None,
    Field(description="true = only topped (sent) entries, false = only unsuccessful attempts."),
]
StartDate = Annotated[
    str | None,
    Field(description="Inclusive start date, YYYY-MM-DD, in the user's timezone."),
]
EndDate = Annotated[
    str | None,
    Field(description="Inclusive end date, YYYY-MM-DD, in the user's timezone."),
]
Period = Annotated[
    Literal["month", "week"],
    Field(description="Bucket size for the progression: calendar month or ISO week."),
]
SessionAngle = Annotated[
    int, Field(description="Wall angle in degrees the session will be climbed at (e.g. 30).")
]
TargetGrade = Annotated[
    str | None,
    Field(
        description=(
            "Grade to build the session around: V-scale ('V4'), Font ('6B', '6B+') or a "
            "Kilter difficulty id. Omit to infer it from the hardest send at that angle."
        )
    ),
]
Warmups = Annotated[
    int,
    Field(description="Number of warm-up climbs (sent, well below target).", ge=0, le=50),
]
MainCount = Annotated[
    int,
    Field(description="Number of climbs at the target grade (±1 Font step).", ge=0, le=50),
]
ProjectCount = Annotated[
    int,
    Field(description="Number of unsent climbs above target to finish with.", ge=0, le=50),
]
IncludeNew = Annotated[
    bool,
    Field(
        description=(
            "Top up each section with climbs the user has never logged, taken from Kilter's "
            "curated list and filtered to climbs that fit their wall. false = logbook only."
        )
    ),
]


def _read_only_tool(server: MCPServer, name: str) -> Callable[[_F], _F]:
    """Register ``fn`` as a read-only tool whose description is its dedented docstring."""

    def decorator(fn: _F) -> _F:
        description = inspect.cleandoc(fn.__doc__ or "")
        return server.tool(name=name, description=description, annotations=READ_ONLY)(fn)

    return decorator


INSTRUCTIONS = (
    "Tools for the authenticated user's own Kilter Board logbook (read-only). "
    "Use them whenever the user asks about their Kilter climbing: sessions, sends, flashes, "
    "projects, attempts, wall angles, grades, pyramids, hardest climbs, progress over time, "
    "or planning an upcoming session (kilter_plan_session builds a warm-up/main/projects "
    "playlist from climbs they have already logged at that angle). "
    "'grade' / 'font_grade' / 'label' are Kilter's consensus grade for the climb (V-scale, "
    "Font scale, and both as e.g. '6A/V3'). Kilter's scale is finer than the V-scale: several "
    "Font grades share one V-grade (4A, 4B and 4C are all V0), so when a user's sends all show "
    "the same V-grade, report Font grades instead; outputs include a 'grade_note' when this "
    "applies. 'my_grade' and 'my_rating' (1-5 stars) are the user's own opinion when they "
    "logged one, otherwise null. 'status' is 'flash' (topped first try), 'send' (topped) or "
    "'attempt' (not topped). Angles are wall angles in degrees (e.g. 20, 30, 40). Dates and "
    "sessions use the user's timezone, reported in the 'timezone' field."
)


# One agent question usually fans out into several tool calls within a few seconds. Serve
# them from one logbook fetch so Kilter sees one request per question, not one per tool.
LOGS_CACHE_TTL_SECONDS = 60.0

# Repeat suggestions need one small request per sent climb not yet graded at the target
# angle. Bound the burst so a thousand-climb logbook cannot hammer Kilter on one tool call.
REPEAT_STATS_PER_CALL = 40
REPEAT_STATS_CONCURRENCY = 4


class KilterService:
    """Owns the single client for the process and turns failures into safe messages."""

    def __init__(
        self,
        client_factory: Any = None,
        *,
        cache_ttl: float = LOGS_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        tz: tzinfo | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_factory
        self._client: KilterClient | None = None
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._tz = tz
        self._cached: Logbook | None = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()
        # Catalogue state, fetched the first time a tool needs it and kept for the process
        # lifetime: the curated list is ~5 MB and changes slowly, the wall profile hardly ever.
        self._catalogue_lock = asyncio.Lock()
        self._wall: WallProfile | None = None
        self._logged: Catalogue | None = None
        self._curated: Catalogue | None = None
        # Per-climb stats fetched for repeat suggestions, keyed by climb uuid.
        self._climb_stats: dict[str, list[ClimbStat]] = {}

    @staticmethod
    def _default_factory() -> KilterClient:
        return KilterClient(Settings.from_env())

    @property
    def tz(self) -> tzinfo:
        """User's timezone for grouping climbs into days. Resolved lazily so a bad
        KILTER_TIMEZONE is reported as a tool error, not a crash at startup."""
        if self._tz is None:
            try:
                self._tz = resolve_timezone()
            except ConfigError as exc:
                raise ToolError(str(exc)) from None
        return self._tz

    @property
    def tz_name(self) -> str:
        return timezone_name(self.tz)

    def _get_client(self) -> KilterClient:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except ConfigError as exc:
                raise ToolError(str(exc)) from None
        return self._client

    async def load(self) -> tuple[list[LogEntry], GradeTable]:
        client = self._get_client()
        try:
            grades = await client.get_grades()
            logbook = await self._logbook(client)
        except (AuthError, KilterAPIError) as exc:
            raise ToolError(str(exc)) from None
        return logbook.entries, grades

    async def _logbook(self, client: KilterClient) -> Logbook:
        # The lock makes concurrent tool calls share one fetch instead of racing.
        async with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._cached_at < self._cache_ttl:
                return self._cached
            logbook = await client.get_logbook()
            self._cached = logbook
            self._cached_at = now
            return logbook

    async def new_climbs(
        self, logs: list[LogEntry], *, angle: int
    ) -> tuple[list[Candidate], WallProfile | None, list[str]]:
        """Climbs not logged at ``angle`` that the planner may suggest there.

        Repeats first (climbs the user has sent at other angles, graded at ``angle``), then
        curated climbs new to them that fit the wall. Returns ``(candidates, wall, notes)``;
        notes explain anything the agent should relay (catalogue down, wall not inferable,
        repeat grades still loading). Never raises: a planner that can still work from the
        logbook should not fail because the catalogue is down.
        """
        client = self._get_client()
        notes: list[str] = []
        try:
            async with self._catalogue_lock:
                if self._logged is None:
                    self._logged = await client.get_logged_climbs()
                    self._wall = wall_profile(self._logged)
                if self._curated is None:
                    self._curated = await client.get_curated_climbs()
        except (AuthError, KilterAPIError) as exc:
            return [], self._wall, [f"Catalogue unavailable ({exc}); using your logbook only."]

        stats = stats_index(self._curated, self._logged)
        notes.extend(await self._load_repeat_stats(client, logs, stats, angle=angle))
        repeats = repeat_candidates(logs, self._logged.climbs, stats, angle=angle)

        fresh: list[Candidate] = []
        if self._wall is None:
            notes.append(
                "Could not infer your wall from the logbook (no logged climbs with edge data), "
                "so no climbs new to you are suggested; repeats of your own sends still are."
            )
        else:
            logged = {e.climb_uuid for e in logs}
            fresh = candidates(self._curated, self._wall, angle=angle, exclude=logged)
        return repeats + fresh, self._wall, notes

    async def _load_repeat_stats(
        self,
        client: KilterClient,
        logs: list[LogEntry],
        stats: dict[tuple[str, int], ClimbStat],
        *,
        angle: int,
    ) -> list[str]:
        """Fill ``stats`` with per-climb lookups for sent climbs not yet graded at ``angle``.

        One request per climb (~3 KB), capped per call so a large logbook cannot trigger
        hundreds of requests at once; results are cached for the process lifetime, so a
        second call picks up where the first stopped. Failures are noted, not raised.
        """
        logged_here = {e.climb_uuid for e in logs if e.angle == angle}
        wanted: dict[str, tuple[int, int, float]] = {}
        for e in logs:
            if not e.topped or e.angle is None or e.angle == angle or e.climb_uuid in logged_here:
                continue
            if (e.climb_uuid, angle) in stats or e.climb_uuid in self._climb_stats:
                continue
            rating, sends, latest = wanted.get(e.climb_uuid, (0, 0, 0.0))
            wanted[e.climb_uuid] = (
                max(rating, e.user_rating or 0),
                sends + 1,
                max(latest, e.created_at.timestamp() if e.created_at else 0.0),
            )
        for uuid, rows in self._climb_stats.items():
            for s in rows:
                stats.setdefault((uuid, s.angle), s)

        # Most-wanted first: climbs the user rated, then repeated, then recent.
        queue = sorted(wanted, key=lambda u: wanted[u], reverse=True)
        batch, deferred = queue[:REPEAT_STATS_PER_CALL], queue[REPEAT_STATS_PER_CALL:]
        failures = 0
        semaphore = asyncio.Semaphore(REPEAT_STATS_CONCURRENCY)

        async def fetch(uuid: str) -> None:
            nonlocal failures
            async with semaphore:
                try:
                    self._climb_stats[uuid] = await client.get_climb_stats(uuid)
                except (AuthError, KilterAPIError):
                    failures += 1

        await asyncio.gather(*(fetch(u) for u in batch))
        for uuid in batch:
            for s in self._climb_stats.get(uuid, []):
                stats.setdefault((uuid, s.angle), s)

        notes: list[str] = []
        if deferred:
            notes.append(
                f"Checked grades at {angle}° for {len(batch)} of your sent climbs; "
                f"{len(deferred)} more are not graded yet. Ask again to consider them too."
            )
        if failures:
            notes.append(
                f"Could not load grades at {angle}° for {failures} of your sent climbs; they "
                "were left out of the repeat suggestions."
            )
        return notes

    def envelope(self, logs: list[LogEntry], body: dict[str, Any]) -> dict[str, Any]:
        """Common fields on every tool response.

        ``timezone`` and ``total_entries`` let the agent judge scope without another call;
        ``data_warning`` appears only when the logbook may be incomplete (see
        ``KilterClient.get_logbook``).
        """
        out: dict[str, Any] = {"timezone": self.tz_name, "total_entries": len(logs)}
        if self.data_warning:
            out["data_warning"] = self.data_warning
        out.update(body)
        return out

    @property
    def data_warning(self) -> str | None:
        return self._cached.warning if self._cached is not None else None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _check_limit(limit: int | None, default: int, maximum: int = 500) -> int:
    if limit is None:
        return default
    if limit < 1:
        raise ToolError("limit must be a positive integer.")
    return min(limit, maximum)


def _date_range(start_date: str | None, end_date: str | None, tz: tzinfo) -> tuple[Any, Any]:
    try:
        return (
            parse_date_arg(start_date, tz=tz),
            parse_date_arg(end_date, end_of_day=True, tz=tz),
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def create_server(service: KilterService | None = None) -> MCPServer:
    svc = service or KilterService()
    server = MCPServer(
        name="kilter",
        title="Kilter Board logbook",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/wmmg101/kilter-mcp",
    )

    @_read_only_tool(server, "kilter_get_logs")
    async def kilter_get_logs(
        limit: Limit = 25,
        angle: Angle = None,
        topped: Topped = None,
        start_date: StartDate = None,
        end_date: EndDate = None,
    ) -> dict[str, Any]:
        """Return the user's Kilter logbook entries, newest first.

        Use when the user asks about recent Kilter climbs, attempts, what they climbed on a
        date, or wants raw history. Each entry is one climb at one wall angle on one date
        with the number of tries in that entry. Filters: limit (default 25, max 500),
        angle (degrees), topped (true=only sends, false=only unsuccessful attempts),
        start_date/end_date (YYYY-MM-DD, inclusive).
        """
        n = _check_limit(limit, 25)
        tz = svc.tz
        start, end = _date_range(start_date, end_date, tz)
        logs, grades = await svc.load()
        rows = filter_logs(logs, angle=angle, topped=topped, start=start, end=end)
        return svc.envelope(
            logs,
            {
                "total_matching": len(rows),
                "returned": min(n, len(rows)),
                "entries": [entry_to_dict(e, grades, tz) for e in rows[:n]],
            },
        )

    @_read_only_tool(server, "kilter_get_sends")
    async def kilter_get_sends(
        limit: Limit = 25,
        angle: Angle = None,
        start_date: StartDate = None,
        end_date: EndDate = None,
    ) -> dict[str, Any]:
        """Return climbs the user has topped (sent), newest first.

        Use when the user asks about their sends, ticks, completed climbs or flashes.
        Entries with status 'flash' were topped on the first try. Filters: limit
        (default 25, max 500), angle (degrees), start_date/end_date (YYYY-MM-DD).
        """
        n = _check_limit(limit, 25)
        tz = svc.tz
        start, end = _date_range(start_date, end_date, tz)
        logs, grades = await svc.load()
        rows = filter_logs(logs, angle=angle, topped=True, start=start, end=end)
        return svc.envelope(
            logs,
            {
                "total_matching": len(rows),
                "returned": min(n, len(rows)),
                "sends": [entry_to_dict(e, grades, tz) for e in rows[:n]],
            },
        )

    @_read_only_tool(server, "kilter_get_projects")
    async def kilter_get_projects(
        angle: Angle = None,
        limit: Limit = 25,
    ) -> dict[str, Any]:
        """Return the user's projects: climbs attempted but never topped at that wall angle.

        Use when the user asks what they are working on, unfinished climbs, or what to try
        next. A climb is a project per (climb, angle); sending it at another angle does not
        remove it. Sorted by most recently tried, then most attempts. Optional angle filter.
        """
        n = _check_limit(limit, 25)
        logs, grades = await svc.load()
        rows = projects(logs, grades, angle=angle, tz=svc.tz)
        return svc.envelope(
            logs,
            {"total_projects": len(rows), "returned": min(n, len(rows)), "projects": rows[:n]},
        )

    @_read_only_tool(server, "kilter_get_summary")
    async def kilter_get_summary() -> dict[str, Any]:
        """Return a compact overview of the user's whole Kilter history.

        Use first when the user asks a broad question ("how is my climbing going?",
        "summarise my Kilter account") or when you need totals: entries, sends, flashes,
        attempts, angles climbed, date range, session count, hardest send/flash and sends
        per grade.
        """
        logs, grades = await svc.load()
        return svc.envelope(logs, summary(logs, grades, tz=svc.tz))

    @_read_only_tool(server, "kilter_get_grade_pyramid")
    async def kilter_get_grade_pyramid(angle: Angle = None) -> dict[str, Any]:
        """Return the user's send pyramid: sends per grade, hardest first, with flash rates.

        Use when the user asks about their grade pyramid, distribution of grades, flash
        rate per grade, or how solid they are at a level. Optional wall-angle filter.
        """
        logs, grades = await svc.load()
        return svc.envelope(logs, grade_pyramid(logs, grades, angle=angle))

    @_read_only_tool(server, "kilter_get_hardest_sends")
    async def kilter_get_hardest_sends(limit: Limit = 10, angle: Angle = None) -> dict[str, Any]:
        """Return the user's hardest sent climbs (unique per climb and angle), hardest first.

        Use when the user asks about their hardest sends, best climbs, max grade, or personal
        bests. Optional wall-angle filter; limit defaults to 10.
        """
        n = _check_limit(limit, 10, maximum=200)
        logs, grades = await svc.load()
        return svc.envelope(
            logs, {"hardest_sends": hardest_sends(logs, grades, limit=n, angle=angle, tz=svc.tz)}
        )

    @_read_only_tool(server, "kilter_get_sessions")
    async def kilter_get_sessions(limit: Limit = 5) -> dict[str, Any]:
        """Return recent climbing sessions (entries grouped by calendar day), newest first.

        Use when the user asks how their last session went, what they climbed yesterday, or
        wants to compare sessions. Each session lists angles, sends, flashes, attempts,
        hardest send and every climb logged that day. limit defaults to 5.
        """
        n = _check_limit(limit, 5, maximum=100)
        logs, grades = await svc.load()
        return svc.envelope(logs, {"sessions": sessions(logs, grades, limit=n, tz=svc.tz)})

    @_read_only_tool(server, "kilter_get_progression")
    async def kilter_get_progression(
        period: Period = "month", angle: Angle = None
    ) -> dict[str, Any]:
        """Return climbing progression over time, oldest period first.

        Use when the user asks how they have progressed, trends, whether they are improving,
        or for a month-by-month/week-by-week view. period is 'month' (default) or 'week'.
        Each period has sessions, sends, unique climbs, flashes, attempts and hardest
        send/flash. Optional wall-angle filter.
        """
        logs, grades = await svc.load()
        try:
            periods = progression(logs, grades, period=period, angle=angle, tz=svc.tz)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return svc.envelope(logs, {"period": period, "angle": angle, "periods": periods})

    @_read_only_tool(server, "kilter_get_angle_stats")
    async def kilter_get_angle_stats() -> dict[str, Any]:
        """Return per-wall-angle statistics so angles can be compared.

        Use when the user asks to compare angles (e.g. 20° vs 40°), which angle they climb
        hardest or most at, or flash rate by angle. One row per angle with sends, flashes,
        attempts, sessions, hardest send/flash and sends per grade.
        """
        logs, grades = await svc.load()
        return svc.envelope(logs, {"angles": angle_stats(logs, grades, tz=svc.tz)})

    @_read_only_tool(server, "kilter_plan_session")
    async def kilter_plan_session(
        angle: SessionAngle,
        target_grade: TargetGrade = None,
        warmups: Warmups = 4,
        main: MainCount = 6,
        projects: ProjectCount = 2,
        include_new: IncludeNew = True,
    ) -> dict[str, Any]:
        """Build a playlist of climbs for an upcoming session at a given wall angle and grade.

        Use when the user is planning a session ("I'm climbing at 30° tomorrow, what should
        I do?", "make me a V4 session at 40°", "give me a warm-up and some projects").
        Returns ordered sections: 'warmup' (climbs 2-7 Font steps below target, easiest
        first), 'main' (climbs within a Font step of target: repeats to consolidate, then
        unsent ones) and 'projects' (climbs 2-5 steps above target). Climbs from the user's
        own logbook at that angle come first in each section ('source': 'logbook', with their
        history). When a section runs short it is topped up, first with repeats ('status':
        'repeat': climbs they have sent at other angles, listed in 'sent_at', graded at this
        angle by the community), then with climbs they have never logged ('status': 'new',
        'source': 'catalogue') chosen from Kilter's curated list by community quality and
        ascents and filtered to climbs that fit their wall. Every climb has a one-line
        'reason'. Read 'notes' for gaps (no history at that angle, inferred target, catalogue
        unavailable, repeat grades still loading). target_grade accepts 'V4', '6B+' or a
        difficulty id; omit it to use the hardest send at that angle. The first call that
        needs the catalogue downloads it (a few MB plus one small request per sent climb);
        later calls reuse it.
        """
        logs, grades = await svc.load()
        target_ids: list[int] | None = None
        if target_grade is not None and target_grade.strip():
            matches = grades.resolve(target_grade)
            if not matches:
                raise ToolError(
                    f"Unknown grade {target_grade!r}. Use a V-grade like 'V4', a Font grade "
                    "like '6B' or '6B+', or a Kilter difficulty id (1-39)."
                )
            target_ids = [g.id for g in matches]
        fresh: list[Candidate] = []
        wall: WallProfile | None = None
        extra_notes: list[str] = []
        if include_new:
            fresh, wall, extra_notes = await svc.new_climbs(logs, angle=angle)
        plan = session_playlist(
            logs,
            grades,
            angle=angle,
            target_ids=target_ids,
            warmups=warmups,
            main=main,
            projects=projects,
            new_climbs=fresh,
            tz=svc.tz,
        )
        plan["notes"].extend(extra_notes)
        plan["wall"] = wall.to_dict() if wall else None
        return svc.envelope(logs, plan)

    return server


HELP = (
    f"kilter-mcp {__version__} - unofficial MCP server for your Kilter Board logbook.\n\n"
    "This program speaks MCP over stdio and is meant to be launched by an MCP client such as\n"
    "Kiro, not run by hand. Configure it in your Kiro mcp.json with KILTER_USERNAME and\n"
    "KILTER_PASSWORD in the env section. See https://github.com/wmmg101/kilter-mcp\n\n"
    "Options:\n"
    "  --check     Log in with the configured credentials, fetch the logbook and print a\n"
    "              short diagnostic (no entries, no secrets). Exit code 0 on success.\n"
    "  --version   Print the version.\n"
)


async def run_check(service: KilterService | None = None) -> tuple[int, str]:
    """Diagnostic used by ``kilter-mcp --check``. Returns (exit_code, report).

    Prints only counts and configuration facts, never entries or credentials, so the output is
    safe to paste into a bug report.
    """
    svc = service or KilterService()
    lines = [f"kilter-mcp {__version__}"]
    try:
        username = Settings.from_env().username
    except ConfigError as exc:
        return 1, "\n".join([*lines, f"config:    FAILED - {exc}"])
    lines.append(f"config:    ok (username {_mask(username)})")
    try:
        lines.append(f"timezone:  {svc.tz_name}")
        logs, grades = await svc.load()
    except ToolError as exc:
        return 1, "\n".join([*lines, f"kilter:    FAILED - {exc}"])
    finally:
        await svc.aclose()
    days = {d for d in (e.local_date(svc.tz) for e in logs) if d}
    newest = max(days) if days else "n/a"
    lines.append(f"grades:    {len(grades.grades)} grades from {grades.source}")
    lines.append(
        f"logbook:   {len(logs)} entries, {sum(1 for e in logs if e.topped)} sends, "
        f"{len(days)} sessions, most recent {newest}"
    )
    warning = svc.data_warning
    if warning:
        lines.append(f"warning:   {warning}")
    lines.append("result:    ok")
    return 0, "\n".join(lines)


def _mask(username: str) -> str:
    """Show enough of the username to spot a typo without printing it whole."""
    if "@" in username:
        local, _, domain = username.partition("@")
        return f"{local[:2]}***@{domain}"
    return f"{username[:2]}***"


def main(argv: list[str] | None = None) -> None:
    """Console entry point: ``kilter-mcp``. Kiro runs this over stdio."""
    args = sys.argv[1:] if argv is None else argv
    if any(a in ("-h", "--help") for a in args):
        sys.stdout.write(HELP)
        return
    if any(a in ("-V", "--version") for a in args):
        sys.stdout.write(f"kilter-mcp {__version__}\n")
        return
    if "--check" in args:
        code, report = asyncio.run(run_check())
        sys.stdout.write(report + "\n")
        sys.exit(code)
    server = create_server()
    try:
        server.run("stdio")
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
