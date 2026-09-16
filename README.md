# kilter-mcp

<!-- mcp-name: io.github.wmmg101/kilter-mcp -->

An unofficial [MCP](https://modelcontextprotocol.io) server that lets your AI agent read and
analyse **your own Kilter Board logbook**.

Add it to [Kiro](https://kiro.dev) (or [another MCP client](docs/clients.md)), give it your
Kilter login, and then just talk to the agent:

> How did my last Kilter session go?
> What are my current projects at 30°?
> Compare my climbing at 20° and 40°.
> Show my grade pyramid. What are my hardest sends? Am I progressing?

Read-only. Nothing is written to your Kilter account.

## Add to Kiro + Other Clients (Claude, Codex, Cursor etc.)

[![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=kilter&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22kilter-mcp%22%5D%2C%22env%22%3A%7B%22KILTER_USERNAME%22%3A%22%24%7BKILTER_USERNAME%7D%22%2C%22KILTER_PASSWORD%22%3A%22%24%7BKILTER_PASSWORD%7D%22%7D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)

Click the button (Kiro shows a confirmation dialog first), then jump to step 3. Or do it by
hand:

1. Open your MCP config in Kiro: command palette → **Kiro: Open user MCP config (JSON)**
   (or the workspace one, `.kiro/settings/mcp.json`).
2. Add the server:

```json
{
  "mcpServers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${KILTER_USERNAME}",
        "KILTER_PASSWORD": "${KILTER_PASSWORD}"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

3. Provide your Kilter credentials. Either:
   - set `KILTER_USERNAME` and `KILTER_PASSWORD` as environment variables (Kiro expands
     `${VAR}` after you approve the variables in **Settings → Mcp Approved Env Vars**; see
     [how to set them on macOS, Linux and Windows](docs/clients.md#setting-the-environment-variables)), or
   - replace the `${...}` placeholders with the literal values. Only do this in the *user*
     config (`~/.kiro/settings/mcp.json`), never in a workspace config you might commit.
4. Save. Kiro starts the server and the `kilter_*` tools appear. Ask away.

Requirements: [`uv`](https://docs.astral.sh/uv/) installed (`uvx` comes with it). Python is
handled by `uv`.

Kiro CLI users can do the same with
`kiro-cli mcp add --name kilter --scope global --command uvx --args kilter-mcp --env KILTER_USERNAME=... --env KILTER_PASSWORD=...`.

### Optional: timezone

Sessions are grouped by calendar day in your timezone, which is detected from the machine
running the server. If that is wrong (for example a remote or containerised host), add
`"KILTER_TIMEZONE": "Europe/Rome"` (any IANA name) to the `env` block. Every response states
the timezone it used.

### Other clients

kilter-mcp is a standard local MCP server, so it also works in Claude Desktop, Claude Code,
Codex, Cursor, VS Code, Windsurf, Gemini CLI and Zed. Copy-paste configs for each are in
[docs/clients.md](docs/clients.md). (ChatGPT's chat interface only accepts remote HTTP
servers, so it is not supported directly; details in the same document.)

## Example questions

```text
Show my latest Kilter session.
What did I climb last Saturday?
List my sends from this month at 40°.
What are my current projects?
Which projects have I put the most attempts into?
Show my grade pyramid at 30°.
What is my flash rate per grade?
What are my five hardest sends?
Compare my climbing at 20° and 30°.
How has my climbing progressed over the last six months?
```

## Tools the agent gets

| Tool | What it returns |
|---|---|
| `kilter_get_logs` | Logbook entries (filter by limit, angle, topped, date range) |
| `kilter_get_sends` | Topped climbs, newest first |
| `kilter_get_projects` | Climbs attempted but never topped at that angle |
| `kilter_get_summary` | Totals, angles, date range, hardest send/flash, sends by grade |
| `kilter_get_grade_pyramid` | Sends per grade with flash rates |
| `kilter_get_hardest_sends` | Hardest unique sends |
| `kilter_get_sessions` | Entries grouped by day |
| `kilter_get_progression` | Month-by-month or week-by-week trend |
| `kilter_get_angle_stats` | Per-angle comparison |

All tools are read-only and only ever see the account whose credentials you configured.

### How the data is interpreted

- One logbook entry is one climb at one wall angle on one date, with the number of tries in
  that entry. Re-logging a climb creates a new entry.
- `status` is `flash` (topped first try), `send` (topped) or `attempt` (not topped).
- A project is a `(climb, angle)` pair with attempts but no top at that angle.
- Grades are Kilter's current consensus grade for the climb at that angle. `grade` is the
  V-scale and `font_grade` the Font scale; the raw `difficulty_id` (1-39) is always included.
  The grade table is fetched from Kilter at startup with an embedded fallback.
- If you rated a climb yourself in the app, `my_grade` and `my_rating` (1-5 stars) carry your
  opinion; otherwise they are null.
- Dates are reported in your timezone (see above). A session is one calendar day.

## Security and privacy

- Your username and password are only used to obtain an OAuth token from Kilter's login
  server (`idp.kiltergrips.com`). They never leave your machine otherwise.
- Tokens are kept in memory for the life of the MCP process and are never written to disk.
- Credentials and tokens are never included in tool output, logs or error messages.
- The server makes requests only to `idp.kiltergrips.com` and `portal.kiltergrips.com`.
- Also available in the [MCP Registry](https://registry.modelcontextprotocol.io) as
  `io.github.wmmg101/kilter-mcp`.
- Prefer the `${KILTER_PASSWORD}` form so the password lives in your shell environment or
  secret manager rather than in a JSON file.
- Your logbook is fetched at most once a minute, however many tools the agent calls, and
  Kilter's rate-limit responses are respected.

Full details and how to report a problem privately: [SECURITY.md](SECURITY.md).

## Disclaimer

kilter-mcp is an unofficial community project and is not affiliated with or endorsed by
Kilter Grips. It talks to the same endpoints the Kilter app uses; these are not a documented
public API and may change or stop working at any time. Kilter's Terms of Use restrict
access to their services outside the official apps; use this project at your own discretion,
only with your own account, and keep request volume low (the server fetches your logbook
once per tool call).

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup (`git clone`, `uv sync`, `uv run pytest`), architecture notes, and how to
add a tool.

## License

[MIT](LICENSE)
