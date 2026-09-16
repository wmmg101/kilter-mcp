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
from typing import Any, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

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
    sessions,
    summary,
)
from kilter_mcp.auth import AuthError
from kilter_mcp.client import KilterAPIError, KilterClient
from kilter_mcp.config import ConfigError, Settings, resolve_timezone, timezone_name
from kilter_mcp.grades import GradeTable
from kilter_mcp.models import LogEntry

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

_F = TypeVar("_F", bound=Callable[..., Any])


def _read_only_tool(server: MCPServer, name: str) -> Callable[[_F], _F]:
    """Register ``fn`` as a read-only tool whose description is its dedented docstring."""

    def decorator(fn: _F) -> _F:
        description = inspect.cleandoc(fn.__doc__ or "")
        return server.tool(name=name, description=description, annotations=READ_ONLY)(fn)

    return decorator


INSTRUCTIONS = (
    "Tools for the authenticated user's own Kilter Board logbook (read-only). "
    "Use them whenever the user asks about their Kilter climbing: sessions, sends, flashes, "
    "projects, attempts, wall angles, grades, pyramids, hardest climbs or progress over time. "
    "'grade' / 'font_grade' are Kilter's consensus grade for the climb (V-scale / Font scale); "
    "'my_grade' and 'my_rating' (1-5 stars) are the user's own opinion when they logged one, "
    "otherwise null. 'status' is 'flash' (topped first try), 'send' (topped) or 'attempt' "
    "(not topped). Angles are wall angles in degrees (e.g. 20, 30, 40). Dates and sessions "
    "use the user's timezone, reported in the 'timezone' field."
)


# One agent question usually fans out into several tool calls within a few seconds. Serve
# them from one logbook fetch so Kilter sees one request per question, not one per tool.
LOGS_CACHE_TTL_SECONDS = 60.0


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
        self._cached_logs: list[LogEntry] | None = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

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
            logs = await self._logs(client)
        except (AuthError, KilterAPIError) as exc:
            raise ToolError(str(exc)) from None
        return logs, grades

    async def _logs(self, client: KilterClient) -> list[LogEntry]:
        # The lock makes concurrent tool calls share one fetch instead of racing.
        async with self._lock:
            now = self._clock()
            if self._cached_logs is not None and now - self._cached_at < self._cache_ttl:
                return self._cached_logs
            logs = await client.get_logs()
            self._cached_logs = logs
            self._cached_at = now
            return logs

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
        limit: int | None = 50,
        angle: int | None = None,
        topped: bool | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Return the user's Kilter logbook entries, newest first.

        Use when the user asks about recent Kilter climbs, attempts, what they climbed on a
        date, or wants raw history. Each entry is one climb at one wall angle on one date
        with the number of tries in that entry. Filters: limit (default 50, max 500),
        angle (degrees), topped (true=only sends, false=only unsuccessful attempts),
        start_date/end_date (YYYY-MM-DD, inclusive).
        """
        n = _check_limit(limit, 50)
        tz = svc.tz
        start, end = _date_range(start_date, end_date, tz)
        logs, grades = await svc.load()
        rows = filter_logs(logs, angle=angle, topped=topped, start=start, end=end)
        return {
            "timezone": svc.tz_name,
            "total_matching": len(rows),
            "returned": min(n, len(rows)),
            "entries": [entry_to_dict(e, grades, tz) for e in rows[:n]],
        }

    @_read_only_tool(server, "kilter_get_sends")
    async def kilter_get_sends(
        limit: int | None = 50,
        angle: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Return climbs the user has topped (sent), newest first.

        Use when the user asks about their sends, ticks, completed climbs or flashes.
        Entries with status 'flash' were topped on the first try. Filters: limit
        (default 50, max 500), angle (degrees), start_date/end_date (YYYY-MM-DD).
        """
        n = _check_limit(limit, 50)
        tz = svc.tz
        start, end = _date_range(start_date, end_date, tz)
        logs, grades = await svc.load()
        rows = filter_logs(logs, angle=angle, topped=True, start=start, end=end)
        return {
            "timezone": svc.tz_name,
            "total_matching": len(rows),
            "returned": min(n, len(rows)),
            "sends": [entry_to_dict(e, grades, tz) for e in rows[:n]],
        }

    @_read_only_tool(server, "kilter_get_projects")
    async def kilter_get_projects(
        angle: int | None = None,
        limit: int | None = 50,
    ) -> dict[str, Any]:
        """Return the user's projects: climbs attempted but never topped at that wall angle.

        Use when the user asks what they are working on, unfinished climbs, or what to try
        next. A climb is a project per (climb, angle); sending it at another angle does not
        remove it. Sorted by most recently tried, then most attempts. Optional angle filter.
        """
        n = _check_limit(limit, 50)
        logs, grades = await svc.load()
        rows = projects(logs, grades, angle=angle, tz=svc.tz)
        return {"total_projects": len(rows), "returned": min(n, len(rows)), "projects": rows[:n]}

    @_read_only_tool(server, "kilter_get_summary")
    async def kilter_get_summary() -> dict[str, Any]:
        """Return a compact overview of the user's whole Kilter history.

        Use first when the user asks a broad question ("how is my climbing going?",
        "summarise my Kilter account") or when you need totals: entries, sends, flashes,
        attempts, angles climbed, date range, session count, hardest send/flash and sends
        per grade.
        """
        logs, grades = await svc.load()
        return {"timezone": svc.tz_name, **summary(logs, grades, tz=svc.tz)}

    @_read_only_tool(server, "kilter_get_grade_pyramid")
    async def kilter_get_grade_pyramid(angle: int | None = None) -> dict[str, Any]:
        """Return the user's send pyramid: sends per grade, hardest first, with flash rates.

        Use when the user asks about their grade pyramid, distribution of grades, flash
        rate per grade, or how solid they are at a level. Optional wall-angle filter.
        """
        logs, grades = await svc.load()
        return grade_pyramid(logs, grades, angle=angle)

    @_read_only_tool(server, "kilter_get_hardest_sends")
    async def kilter_get_hardest_sends(
        limit: int | None = 10, angle: int | None = None
    ) -> dict[str, Any]:
        """Return the user's hardest sent climbs (unique per climb and angle), hardest first.

        Use when the user asks about their hardest sends, best climbs, max grade, or personal
        bests. Optional wall-angle filter; limit defaults to 10.
        """
        n = _check_limit(limit, 10, maximum=200)
        logs, grades = await svc.load()
        return {"hardest_sends": hardest_sends(logs, grades, limit=n, angle=angle, tz=svc.tz)}

    @_read_only_tool(server, "kilter_get_sessions")
    async def kilter_get_sessions(limit: int | None = 5) -> dict[str, Any]:
        """Return recent climbing sessions (entries grouped by calendar day), newest first.

        Use when the user asks how their last session went, what they climbed yesterday, or
        wants to compare sessions. Each session lists angles, sends, flashes, attempts,
        hardest send and every climb logged that day. limit defaults to 5.
        """
        n = _check_limit(limit, 5, maximum=100)
        logs, grades = await svc.load()
        return {"timezone": svc.tz_name, "sessions": sessions(logs, grades, limit=n, tz=svc.tz)}

    @_read_only_tool(server, "kilter_get_progression")
    async def kilter_get_progression(
        period: str = "month", angle: int | None = None
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
        return {"timezone": svc.tz_name, "period": period, "angle": angle, "periods": periods}

    @_read_only_tool(server, "kilter_get_angle_stats")
    async def kilter_get_angle_stats() -> dict[str, Any]:
        """Return per-wall-angle statistics so angles can be compared.

        Use when the user asks to compare angles (e.g. 20° vs 40°), which angle they climb
        hardest or most at, or flash rate by angle. One row per angle with sends, flashes,
        attempts, sessions, hardest send/flash and sends per grade.
        """
        logs, grades = await svc.load()
        return {"angles": angle_stats(logs, grades, tz=svc.tz)}

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
