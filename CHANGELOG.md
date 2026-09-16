# Changelog

All notable changes to kilter-mcp. Versions follow [Semantic Versioning](https://semver.org).

## 0.2.1 - 2026-09-16

### Security
- After Kilter rejects a login (wrong password, disabled account) the server does not retry
  for 60 seconds, however many tool calls arrive, and reports the original reason plus the
  wait. Prevents a mistyped password from triggering Keycloak's brute-force lockout on the
  user's Kilter account. Network errors and 5xx responses do not trigger the cooldown.
- `SECURITY.md` now states the two limitations inherent to stdio MCP servers: credentials
  live in the process environment, and Kilter offers no read-only tokens.

### Added
- `kilter-mcp --check`: logs in, fetches the logbook and prints counts, timezone and a masked
  username. No entries, no secrets. Exit code 0 on success, 1 otherwise.
- CI now runs on Windows and macOS as well as Linux (Python 3.10, 3.12, 3.14).
- Dependabot for Python dependencies and GitHub Actions; issue templates with a
  "never paste credentials" warning; `CODE_OF_CONDUCT.md`.

## 0.2.0 - 2026-09-16

### Changed
- Sessions, progression periods, project dates and date filters now use the user's timezone
  instead of UTC, so an evening session no longer splits in two at UTC midnight. The zone is
  detected from the host (`TZ`, then `/etc/localtime`) and can be overridden with
  `KILTER_TIMEZONE` (IANA name). Responses include a `timezone` field.
- Entry `date` values are now local ISO 8601 timestamps with offset (was UTC `...Z`).

### Added
- `my_difficulty_id`, `my_grade` and `my_rating` on every entry: the user's own grade and
  1-5 star rating when Kilter returns an embedded `climbRating`, otherwise null.
- `server.json` and registry metadata; published to the MCP Registry as
  `io.github.wmmg101/kilter-mcp`.
- `tzdata` dependency on Windows, where the system has no zoneinfo database.

## 0.1.1 - 2026-09-16

### Changed
- The logbook is now fetched at most once per 60 seconds and shared across tool calls, so a
  question that fans out into several tools costs Kilter one request. Concurrent calls share
  a single in-flight fetch. Failed fetches are not cached.
- HTTP 429 responses from Kilter are retried after the `Retry-After` delay (delta-seconds or
  HTTP-date), at most twice and only for delays up to 30 seconds. Otherwise the agent gets a
  clear "rate-limited, try again in a minute" error instead of a generic failure.
- Release workflow is idempotent (`skip-existing`), so re-pushing an existing tag is a no-op.

### Added
- `SECURITY.md` describing credential handling and private vulnerability reporting.
- Secret scanning, push protection and Dependabot security updates enabled on the repo.

## 0.1.0 - 2026-09-16

First release.

- Read-only MCP server over your own Kilter Board logbook, launched with `uvx kilter-mcp`.
- Nine tools: `kilter_get_logs`, `kilter_get_sends`, `kilter_get_projects`,
  `kilter_get_summary`, `kilter_get_grade_pyramid`, `kilter_get_hardest_sends`,
  `kilter_get_sessions`, `kilter_get_progression`, `kilter_get_angle_stats`.
- Keycloak password grant with in-memory tokens, refresh and rotation.
- Grades resolved from Kilter's public grade table with an embedded fallback.
- Credentials and tokens never appear in output, logs or errors.
- Setup guides for Kiro and other MCP clients.
