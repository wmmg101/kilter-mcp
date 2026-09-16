# kilter-mcp — Design

## Layers

```
Kiro agent
    │  MCP (stdio)
    ▼
server.py        MCP tools: thin wrappers, argument parsing, structured output
    │
    ▼
analytics.py     pure functions over list[LogEntry] (filter, group, summarise)
grades.py        difficulty id → grade lookup (GradeTable), embedded fallback
    │
    ▼
models.py        LogEntry, Grade dataclasses; parse from API dicts
    │
    ▼
client.py        KilterClient: get_logs(), get_grades(); adds bearer header
auth.py          TokenManager: password grant, in-memory tokens, refresh + rotation
config.py        Settings from KILTER_USERNAME / KILTER_PASSWORD
endpoints.py     all URLs in one place
```

Rules
- `server.py` never touches OAuth or raw HTTP.
- `analytics.py` and `grades.py` never do I/O.
- `client.py` / `auth.py` never import `mcp`.
- Only `config.py`/`auth.py`/`client.py` ever see the password or tokens.

## Runtime

- `kilter-mcp` console script → `kilter_mcp.server:main` → `MCPServer.run("stdio")`.
- Server holds one lazily-created `KilterClient` for the process lifetime.
- `KilterService` caches the logbook for 60 s (`LOGS_CACHE_TTL_SECONDS`) behind an
  `asyncio.Lock`, so one agent question that fans out into several tools costs one fetch and
  concurrent calls share an in-flight fetch. Failed fetches are not cached. Grades are fetched
  once per process.
- `KilterClient` retries HTTP 429 after `Retry-After` (delta-seconds or HTTP-date), at most
  `max_rate_limit_retries` (2) times and only when the delay is ≤ 30 s; otherwise it raises a
  clear rate-limited `KilterAPIError`. `sleep` is injectable for tests.
- Errors are converted to a plain error message (`ToolError`-style string); messages
  never include credentials or headers.

## Auth (auth.py)

```
TokenManager(settings, http)
  .get_access_token()  -> str
      if no token: password_grant()
      elif expiring within 60s: refresh() (on failure → password_grant())
  state: access_token, refresh_token, expires_at (monotonic)
```
Password grant and refresh both POST to the Keycloak token endpoint with
`client_id=kilter`. Refresh response may include a new `refresh_token`; if present it
replaces the stored one.

## Client (client.py)

```
KilterClient(settings, http=None)
  async get_logs() -> list[LogEntry]
  async get_grades() -> GradeTable      (public endpoint, no bearer needed)
  async aclose()
```
`httpx2.AsyncClient` injected for tests via `MockTransport`.

## Models (models.py)

`LogEntry`: climb_uuid, climb_name, angle, attempts, topped, flashed, created_at
(datetime, UTC), difficulty_id (int | None), plus a `status` property
(`flash` / `send` / `attempt`). `LogEntry.from_api(dict)` tolerant of missing fields.
`Grade`: id, v_scale, font_scale, boulder_label, is_listed.

## Analytics (analytics.py)

Pure functions:
- `filter_logs(logs, angle=None, topped=None, start=None, end=None)`
- `sends(logs)`, `projects(logs)` → grouped by `(climb_uuid, angle)` with no topped row
- `summary(logs, grades)`
- `grade_pyramid(logs, grades, angle=None)`
- `hardest_sends(logs, grades, limit, angle)`
- `sessions(logs)` → group by local calendar date of `created_at`
- `progression(logs, grades, period="month")`
- `angle_stats(logs, grades)`

Each returns plain dicts/lists so the server can return them directly as structured
content.

## Tool output shape

Each log entry serialised as:
```json
{"climb_name": "...", "date": "2026-09-15T17:02:44Z", "angle": 20,
 "attempts": 1, "topped": true, "flashed": true, "status": "flash",
 "difficulty_id": 11, "grade": "V0", "font_grade": "4B", "climb_uuid": "..."}
```
`climb_uuid` is kept because projects/sends group on it and later catalogue tools will
need it. Other UUIDs (user/gym/wall/layout) are dropped.

## Security

- `Settings.__repr__` redacts password. `TokenManager` has no `__repr__` leaking tokens.
- `redact()` helper used on any exception text that could carry a URL or header.
- No HTTP request/response logging by default.

## Testing

- `httpx2.MockTransport` handlers simulate Keycloak + portal.
- Fixtures in `tests/fixtures/logs.json` and `grades.json` are synthetic.
- Server tools tested in-process with `mcp.Client(server)`.
- Optional `tests/integration/` guarded by `KILTER_INTEGRATION=1` and real env vars;
  skipped by default and never prints entries.

## Distribution

- `pyproject.toml` with hatchling, `[project.scripts] kilter-mcp = "kilter_mcp.server:main"`.
- Publish to PyPI as `kilter-mcp`; `uvx kilter-mcp` then works.
- README includes Kiro `mcp.json` snippet and an "Add to Kiro" launch link.
