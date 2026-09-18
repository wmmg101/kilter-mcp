# kilter-mcp — Requirements

## Product

A user adds `kilter-mcp` as an MCP server in Kiro, supplies Kilter credentials via the
MCP configuration environment, and the Kiro agent gains read-only tools over that user's
Kilter Board logbook. The MCP server is the product. There is no standalone app, UI,
login command, or database.

## User stories

1. As a Kiro user, I can add `kilter-mcp` with `uvx kilter-mcp` plus
   `KILTER_USERNAME` / `KILTER_PASSWORD` and immediately ask the agent about my climbing.
2. As a Kiro user, I can ask about recent logs, sends, flashes, projects, angles,
   grades and progression without knowing tool names.
3. As a contributor, I can `git clone`, `uv sync`, `uv run pytest` and work on the code
   with mocked Kilter responses and no real account.

## Functional requirements

### Configuration
- R1. Read `KILTER_USERNAME` and `KILTER_PASSWORD` from the process environment.
- R2. If either is missing, tool calls fail with a clear, actionable error; the server
  still starts so Kiro can list tools.
- R2a. Optional `KILTER_TIMEZONE` (IANA name) sets the timezone used to group climbs into
  days; default is the host zone (`TZ`, `/etc/localtime`, then current offset). An invalid
  value is a clear tool error. Every response that involves dates states the timezone.

### Authentication
- R3. Authenticate with the Kilter Keycloak realm using the password grant
  (`client_id=kilter`, `scope=openid offline_access`).
- R4. Hold access and refresh tokens in memory only. Never persist them.
- R5. Refresh the access token before/when it expires using the refresh token; adopt a
  rotated refresh token if Kilter returns one.
- R6. If refresh fails, fall back to a fresh password login once.
- R6a. After Keycloak rejects a password grant (400/401/403), do not attempt another login
  for 60 s; surface the original reason plus the remaining wait. Network and 5xx failures
  are not "rejections" and do not trigger the cooldown. Protects against account lockout.
- R6b. `kilter-mcp --check` prints a diagnostic (masked username, timezone, grade source,
  entry/send/session counts, most recent day) with no entries and no secrets.

### Data
- R7. Fetch logbook rows from `GET /api/logs` and parse them into typed models.
  Unknown fields are ignored; missing optional fields do not crash parsing.
- R8. Fetch the difficulty table from `GET /api/grades` (public) and cache it for the
  process lifetime. Fall back to an embedded copy of the same table if unavailable.
- R9. Map `currentDifficultyId` to `vScale`, `fontScale` and the combined boulder label.
  Always also expose the raw id.

### MCP tools (v0.1)
- R10. `kilter_get_logs(limit, angle, topped, start_date, end_date)` — log entries.
- R11. `kilter_get_sends(limit, angle, start_date, end_date)` — topped entries.
- R12. `kilter_get_projects(angle, limit)` — climbs attempted but never topped at that
  `(climbUuid, angle)`.
- R13. `kilter_get_summary()` — counts, flash count, attempts, angles, date range,
  grade distribution of sends.
- R14. `kilter_get_grade_pyramid(angle)` — sends per grade, with flash counts.
- R15. `kilter_get_hardest_sends(limit, angle)` — top sends by difficulty id.
- R16. `kilter_get_sessions(limit)` — logs grouped by calendar day (a "session").
- R17. `kilter_get_progression(period)` — per-month (or week) sends, max grade, flashes.
- R18. `kilter_get_angle_stats()` — per-angle comparison.
- R19. All tools are read-only and declare `readOnlyHint=True`.
- R20. Tool output is structured (JSON-serialisable dicts), not prose.
- R21. Every response carries `timezone` and `total_entries`; `data_warning` is present only
  when the logbook may be incomplete (paging marker, total > rows, or a row count equal to a
  common page size). `--check` prints the same warning.
- R22. Grade fields are always `difficulty_id`, `grade` (V), `font_grade` and `label`.
  Summary and pyramid add `grade_note` when Font grades outnumber V-grades in the sends.

### Log semantics (drives analytics correctness)
- One `/api/logs` row is a self-contained entry for one climb at one angle on one date.
- `topped=true, flashed=true` → flash; `topped=true, flashed=false` → send;
  `topped=false` → attempt session without a top.
- `attempts` is the number of tries recorded in that entry.
- Repeat sends produce new rows. Same climb can appear at several angles; identity is
  `(climbUuid, angle)`.
- `currentDifficultyId` is the climb's current consensus grade at that angle, not the
  user's personal grade. The user's own opinion, when present, is an embedded `climbRating`
  object (`difficultyGradeId`, `rating` 1-5) and is exposed as `my_grade` / `my_rating`.
- A "session" is a calendar day in the user's timezone (R2a).

## Non-functional requirements

- N1. Credentials, tokens and `Authorization` headers never appear in tool output,
  logs, exceptions or test fixtures.
- N2. Tests run with mocked HTTP only; CI needs no Kilter account.
- N3. Nothing account-specific is hardcoded (no UUIDs, emails, gym ids).
- N4. Runs with `uvx kilter-mcp` and `pip install kilter-mcp && kilter-mcp`.
- N5. Python ≥ 3.10, dependencies: `mcp`, `httpx2` (already required by `mcp`).
- N6. README leads with user setup; contributor docs live in CONTRIBUTING.md.
- N7. README states the project is unofficial and not affiliated with Kilter Grips, and
  that the endpoints are not a documented public API.

## Out of scope for v0.1

Full climb catalogue, PowerSync, write operations (logging climbs), keyring, token
files, SQLite, dashboards, any UI.
