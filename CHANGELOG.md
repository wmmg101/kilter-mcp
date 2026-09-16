# Changelog

All notable changes to kilter-mcp. Versions follow [Semantic Versioning](https://semver.org).

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
