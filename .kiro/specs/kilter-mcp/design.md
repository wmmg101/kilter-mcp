# kilter-mcp — Design

## Layers

```
Kiro agent
    │  MCP (stdio)
    ▼
server.py        MCP tools: thin wrappers, argument parsing, structured output
    │
    ▼
analytics.py     pure functions over list[LogEntry] (filter, group, summarise, plan)
catalogue.py     pure functions over Catalogue: infer the wall, pick candidate climbs
grades.py        difficulty id → grade lookup (GradeTable), embedded fallback
    │
    ▼
models.py        LogEntry, Grade, CatalogClimb, ClimbStat, Catalogue, Box; parse API dicts
    │
    ▼
client.py        KilterClient: get_logs(), get_grades(), get_logged_climbs(),
                 get_curated_climbs(); adds bearer header
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
- `KilterService.new_climbs(logs, angle)` lazily fetches `/api/climbs/logged` (→
  `WallProfile` + climb rows) and `/api/climbs/curated` (~5 MB) behind their own lock, keeps
  both for the process lifetime, then `_load_repeat_stats` fills missing per-angle grades for
  the user's sent climbs via `GET /api/climb-stat/{uuid}` (`REPEAT_STATS_PER_CALL=40`,
  `REPEAT_STATS_CONCURRENCY=4`, cached per uuid). Returns `(repeats + new, wall, notes)`.
  It never raises: a catalogue failure becomes a note and the planner falls back to the
  logbook. Measured on a real account (127 entries, 49 sent climbs): first
  `kilter_plan_session` call ~5 s, later calls ≤0.3 s.
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
  async get_logged_climbs() -> Catalogue   (GET /api/climbs/logged)
  async get_curated_climbs() -> Catalogue  (GET /api/climbs/curated, ~5 MB)
  async get_climb_stats(uuid) -> list[ClimbStat]  (GET /api/climb-stat/{uuid}, all angles)
  async aclose()
```

Catalogue payloads are `{"climbs": [...], "climbStats": [...]}`. Findings from probing the
portal (2026-10-16, real account; shapes only): `/api/climbs/all/{layout}` is 143 MB /
223k climbs for layout "10" and `/api/climb-stat/all/{layout}` 39 MB / 197k rows, so they are
not used. `/api/climbs/?name=` is a server-side paginated name search (`{items, total}`);
`angle`/difficulty query params are ignored. `POST /api/climbs/climbdetails/productName/edges`
(JSON body `productName` + edges, optional `limit`/`offset` in the body) lists climbs inside
an edge box but ignores every filter other than the box. `GET /api/walls*` is 405, so the wall
is inferred from logged climbs' edge boxes. Layout uuids are small numeric strings ("10",
"27"); one wall hosts climbs from several layouts.
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
- `session_playlist(logs, grades, angle, target_ids=None, warmups=4, main=6, projects=2,
  new_climbs=())` → collapses entries per `(climb_uuid, angle)` into one history row
  (`_climb_history`), then fills `warmup` / `main` / `projects` from id bands relative to the
  target range (`WARMUP_BAND=(-7,-2)`, `MAIN_BAND=(-1,1)`, `PROJECT_BAND=(2,5)`; negative
  offsets hang off `lo`, positive off `hi`). Each section is topped up from `new_climbs`
  (`catalogue.Candidate`, already filtered to the angle and the wall, best first); the
  warm-up top-up is re-sorted easiest first. Target inference lives in `_infer_target`. Grade
  labels are resolved to ids in the server via `GradeTable.resolve(text)` (listed ids
  preferred, so "V0" is 4A-4C not 1A-4C).

## Catalogue (catalogue.py)

- `wall_profile(logged: Catalogue) -> WallProfile | None`: dominant product among logged
  climbs with edges, the set of layouts seen, and the union of their edge boxes.
- `candidates(curated, profile, angle, exclude, min_ascents=1) -> list[Candidate]`: one row
  per climb at the angle (duplicates collapsed, preferring graded rows with more ascents),
  listed, not draft, `profile.accepts(climb)`, not excluded; sorted by quality, then ascents.
- `repeat_candidates(logs, climbs, stats, angle) -> list[Candidate]`: climbs sent at other
  angles, not logged at `angle`, with a graded `(uuid, angle)` stat having ≥1 ascent. Each
  carries `sent_at: tuple[PriorSend, ...]`. Falls back to a climb row built from the log entry
  when the catalogue has none (a repeat fits the wall by definition). Ranked by the user's
  rating, then total sends, then quality.
- `Candidate` pairs a `CatalogClimb` with its `ClimbStat` at one angle; `is_repeat` is
  `bool(sent_at)`. The planner's top-up puts repeats before new climbs (stable sort).

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
