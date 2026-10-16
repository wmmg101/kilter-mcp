# Architecture

Layers (top depends on bottom, never the reverse):

```
server.py   → analytics.py, catalogue.py, grades.py, models.py, client.py
analytics.py → catalogue.py (Candidate only), grades.py, models.py (pure, no I/O)
catalogue.py, grades.py → models.py only (pure, no I/O)
client.py   → auth.py, endpoints.py, models.py, config.py
auth.py     → endpoints.py, config.py
```

Rules
- `server.py` contains no OAuth or raw HTTP; it calls `KilterClient` and analytics.
- `analytics.py`/`catalogue.py`/`grades.py` are pure functions over `list[LogEntry]` /
  `Catalogue`; unit-test them without mocks.
- Catalogue reads are `GET /api/climbs/logged` (the user's own climbs with edge boxes, used
  to infer the wall and as climb rows for repeats), `GET /api/climbs/curated` (~5 MB, Kilter's
  featured list, the pool for new climbs) and `GET /api/climb-stat/{uuid}` (per-climb grades
  at every angle, for repeats; bounded to `REPEAT_STATS_PER_CALL` requests per tool call,
  cached per uuid). All cached for the process lifetime. Never fetch `/api/climbs/all/{layout}`
  or `/api/climb-stat/all/{layout}`: they are 40-150 MB per layout and scoped to a climb's
  layout, not the user's wall.
- Top-up order in the planner is repeats (sent elsewhere, `status: "repeat"`) before new
  climbs (`status: "new"`); a climb logged at the target angle is never a top-up there.
- Wall fit is inferred, never assumed: a candidate must share the product, be on a layout the
  user has already climbed, and its edge box must sit inside the union of the user's logged
  climbs' boxes (`catalogue.WallProfile.accepts`). Conservative by design.
- `client.py`/`auth.py` never import `mcp`.
- All URLs live in `endpoints.py`.
- HTTP library is `httpx2` (already a dependency of `mcp`); inject `AsyncClient` so tests
  can use `MockTransport`.
- MCP SDK is `mcp>=2` : `from mcp.server import MCPServer`; tools use
  `annotations=ToolAnnotations(read_only_hint=True)` (snake_case fields; the wire format is
  camelCase); FastMCP no longer exists.
- Tool functions return JSON-serialisable dicts/lists (structured output), not prose.
- Log semantics: one row = one climb at one angle on one date; `topped`/`flashed` give
  status; `attempts` = tries in that entry; identity for grouping is
  `(climb_uuid, angle)`; `currentDifficultyId` is the consensus grade.
- Grades come from `GET /api/grades` (public) with an embedded fallback in `grades.py`.
  Always expose the raw `difficulty_id` alongside any grade label.
- Anything that turns a timestamp into a calendar day or period takes a `tz: tzinfo`
  parameter (default UTC) and the server passes `svc.tz`. Never call `.date()` on a UTC
  datetime for user-facing grouping; use `LogEntry.local_date(tz)`.
- Tests pin `KILTER_TIMEZONE=UTC` via an autouse fixture; override per test when needed.
