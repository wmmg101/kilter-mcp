# Optional container image. Most users run `uvx kilter-mcp` directly; this exists for
# directories that build from a Dockerfile (e.g. Glama) and for anyone who prefers containers.
#
#   docker build -t kilter-mcp .
#   docker run -i --rm -e KILTER_USERNAME -e KILTER_PASSWORD kilter-mcp
#
# The server speaks MCP over stdio, so the MCP client must run the container with `-i`.

FROM python:3.12-slim

# Only the wheel's runtime dependencies; no shell tools, no build step at run time.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && rm -rf /root/.cache

# Non-root; the server writes nothing to disk.
RUN useradd --create-home --uid 1000 kilter
USER kilter

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["kilter-mcp"]
