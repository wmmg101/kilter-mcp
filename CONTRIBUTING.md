# Contributing to kilter-mcp

Thanks for helping. Issues and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/wmmg101/kilter-mcp.git
cd kilter-mcp
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Python ≥ 3.10. `uv` manages the virtualenv and lockfile. No Kilter account is needed for
the normal test suite.

## Architecture

```
Kiro agent
    │  MCP (stdio)
    ▼
server.py      MCP tools: parse args, call the client, run analytics, return dicts
    │
    ▼
analytics.py   pure functions over list[LogEntry]  (no I/O)
grades.py      difficulty id → grade lookup, with embedded fallback table
    │
    ▼
models.py      LogEntry, Grade dataclasses; tolerant parsing of API rows
    │
    ▼
client.py      KilterClient: get_logs(), get_grades(); adds the bearer header
auth.py        TokenManager: password grant, in-memory tokens, refresh + rotation
config.py      Settings from KILTER_USERNAME / KILTER_PASSWORD; redact() helper
endpoints.py   every URL in one place
```

Rules that keep this maintainable:

- `server.py` never does OAuth or raw HTTP.
- `analytics.py` and `grades.py` never do I/O, so they are tested with plain data.
- `client.py` and `auth.py` never import `mcp`.
- Only `config.py`, `auth.py` and `client.py` ever see the password or tokens.
- HTTP goes through `httpx2` (already a dependency of `mcp`); the `AsyncClient` is
  injectable so tests use `httpx2.MockTransport`.
- MCP SDK is `mcp>=2` (`from mcp.server import MCPServer`). `FastMCP` no longer exists.

The Kiro spec in `.kiro/specs/kilter-mcp/` and steering in `.kiro/steering/` describe the
same rules; keep them in sync when behaviour changes.

## Data semantics

These drive every statistic, so please don't change them casually:

- One `/api/logs` row is one climb, at one wall angle, on one date, with `attempts` tries
  in that entry. Re-logging a climb creates a new row.
- `topped && flashed` → `flash`; `topped` → `send`; otherwise `attempt`.
- A climb's identity is `(climbUuid, angle)`. A project is such a pair with attempts but no
  top at that angle.
- `currentDifficultyId` is Kilter's consensus grade for the climb at that angle (1-39).
  Labels come from the public `GET /api/grades`; `grades.FALLBACK_TABLE` is a copy.
  `tests/test_models_and_grades.py` fails if the two drift apart.

## Adding an MCP tool

1. Add a pure function in `analytics.py` that takes `list[LogEntry]` (and `GradeTable`)
   and returns JSON-serialisable dicts/lists. Unit-test it in `tests/test_analytics.py`.
2. Register the tool in `server.py` with `@_read_only_tool(server, "kilter_...")`. The
   docstring is the description the agent sees: say *when* to use it and what each
   argument means. Keep returns structured.
3. Add an end-to-end test in `tests/test_server.py` using `mcp.Client(server)` and the
   `FakeKilter` backend from `tests/conftest.py`.
4. Add the tool to the README table.

## Mocking Kilter

`tests/conftest.py` has `FakeKilter`, an `httpx2.MockTransport` handler that plays both
Keycloak (`/token`) and the portal (`/api/logs`, `/api/grades`). Flip its flags
(`password_ok`, `refresh_ok`, `rotate_refresh`, `reject_tokens`, `logs_status`, ...) to
script failure modes. Synthetic fixtures live in `tests/fixtures/`; keep them synthetic
(`test-climb-1`, `test-user`, etc.).

### Optional real-account check

`tests/integration/test_real_account.py` runs only when `KILTER_INTEGRATION=1` and real
`KILTER_USERNAME`/`KILTER_PASSWORD` are set. It asserts shapes and counts only and never
prints entries. It is skipped in CI and by default.

## Security requirements for every change

See [SECURITY.md](SECURITY.md) for what we promise users; keep it true.

- Never log or return passwords, access tokens, refresh tokens or `Authorization` headers.
- Wrap text from exceptions/responses with `config.redact()` before surfacing it.
- No real UUIDs, emails, tokens or API dumps in fixtures, tests, docs or git history.
- Before pushing: `git diff`, then grep the tree for `Bearer `, `password=`, `@` emails.

## Releasing (maintainers)

Releases are published to PyPI by GitHub Actions through Trusted Publishing; no tokens are
involved and nobody publishes from a laptop.

1. Bump `version` in `pyproject.toml` and `src/kilter_mcp/__init__.py` together, and add a
   section to `CHANGELOG.md`.
2. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.
3. `release.yml` checks the tag matches the version, runs lint and tests, builds, publishes.
4. Create the GitHub release: `gh release create vX.Y.Z --title "kilter-mcp X.Y.Z" --notes-file <(sed -n '/^## X.Y.Z/,/^## /p' CHANGELOG.md)`.
