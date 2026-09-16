# Development conventions

- Tooling: `uv` for everything. `uv sync`, `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format .`.
- Python ≥ 3.10, `from __future__ import annotations`, dataclasses over pydantic for our
  own models (mcp already brings pydantic; we don't need it in our layer).
- Line length 100, ruff rules in `pyproject.toml`.
- Tests: pytest + pytest-asyncio (`asyncio_mode = "auto"`). Mock HTTP with
  `httpx2.MockTransport`. Server tools tested in-process with `mcp.Client(server)`.
- Every new MCP tool needs: a docstring that tells the agent *when* to use it,
  `readOnlyHint=True`, a structured return, and a test.
- Keep the spec in `.kiro/specs/kilter-mcp/` and this steering in sync with code when
  behaviour changes.
- Only commit when asked. Never push unless asked.
