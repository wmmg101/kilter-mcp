# Architecture

Layers (top depends on bottom, never the reverse):

```
server.py   → analytics.py, grades.py, models.py, client.py
analytics.py, grades.py → models.py only (pure, no I/O)
client.py   → auth.py, endpoints.py, models.py, config.py
auth.py     → endpoints.py, config.py
```

Rules
- `server.py` contains no OAuth or raw HTTP; it calls `KilterClient` and analytics.
- `analytics.py`/`grades.py` are pure functions over `list[LogEntry]`; unit-test them
  without mocks.
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
