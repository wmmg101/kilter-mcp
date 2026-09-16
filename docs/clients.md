# Using kilter-mcp with other MCP clients

kilter-mcp is a standard local MCP server (stdio transport), so any MCP client that can launch
a local command can use it. Every setup below boils down to the same three facts:

```text
command:  uvx
args:     kilter-mcp
env:      KILTER_USERNAME, KILTER_PASSWORD
```

Prerequisite everywhere: [`uv`](https://docs.astral.sh/uv/) installed (`uvx` ships with it).

Where a client supports referencing shell environment variables, prefer that over pasting
your password into a JSON file. The exact placeholder syntax differs per client and is
called out in each section. Client config formats change; the official doc for each is
linked so you can double-check.

For Kiro, see the [README](../README.md#add-to-kiro).

---

## Claude Desktop

Local servers are configured in `claude_desktop_config.json`
(Claude menu → Settings → Developer → Edit Config):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "your-kilter-username",
        "KILTER_PASSWORD": "your-kilter-password"
      }
    }
  }
}
```

Claude Desktop does not document environment-variable placeholders, so values are literal.
Restart the app fully after editing. Docs: <https://modelcontextprotocol.io/docs/develop/connect-local-servers>

## Claude Code (CLI)

One command, user scope, forwarding variables from your shell:

```bash
claude mcp add --transport stdio --scope user \
  --env KILTER_USERNAME="$KILTER_USERNAME" --env KILTER_PASSWORD="$KILTER_PASSWORD" \
  kilter -- uvx kilter-mcp
```

Or in a `.mcp.json` (project scope) / `~/.claude.json` (user scope). Claude Code expands
`${VAR}` at launch:

```json
{
  "mcpServers": {
    "kilter": {
      "type": "stdio",
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${KILTER_USERNAME}",
        "KILTER_PASSWORD": "${KILTER_PASSWORD}"
      }
    }
  }
}
```

Docs: <https://code.claude.com/docs/en/mcp>

## ChatGPT

ChatGPT's chat interface (web and the desktop app's chat tab) only connects to **remote**
MCP servers over HTTP, via Developer mode connectors. It cannot launch a local command, so
kilter-mcp is not directly usable there. You would need to host it behind an HTTPS endpoint
(a stdio-to-HTTP bridge plus a tunnel), which also means your Kilter credentials live on
that host. That is out of scope for this project.

The **Codex** side of the ChatGPT desktop app does support local servers; it shares its
config with Codex CLI, so follow the next section.

Docs: <https://developers.openai.com/api/docs/guides/developer-mode/>

## OpenAI Codex (CLI, IDE extension, ChatGPT desktop → Codex)

```bash
codex mcp add kilter --env KILTER_USERNAME="$KILTER_USERNAME" --env KILTER_PASSWORD="$KILTER_PASSWORD" -- uvx kilter-mcp
```

Or in `~/.codex/config.toml`. `env_vars` forwards variables from your shell; `env` sets
literal values:

```toml
[mcp_servers.kilter]
command = "uvx"
args = ["kilter-mcp"]
env_vars = ["KILTER_USERNAME", "KILTER_PASSWORD"]
```

Docs: <https://developers.openai.com/codex/mcp/>

## Cursor

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](cursor://anysphere.cursor-deeplink/mcp/install?name=kilter&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJraWx0ZXItbWNwIl0sImVudiI6eyJLSUxURVJfVVNFUk5BTUUiOiIke2VudjpLSUxURVJfVVNFUk5BTUV9IiwiS0lMVEVSX1BBU1NXT1JEIjoiJHtlbnY6S0lMVEVSX1BBU1NXT1JEfSJ9fQ==)

Or edit `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project). Cursor's placeholder
syntax is `${env:NAME}`:

```json
{
  "mcpServers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${env:KILTER_USERNAME}",
        "KILTER_PASSWORD": "${env:KILTER_PASSWORD}"
      }
    }
  }
}
```

Docs: <https://cursor.com/docs/mcp>

## VS Code (GitHub Copilot)

[Install in VS Code](vscode:mcp/install?%7B%22name%22%3A%22kilter%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22kilter-mcp%22%5D%2C%22env%22%3A%7B%22KILTER_USERNAME%22%3A%22%24%7Binput%3Akilter-username%7D%22%2C%22KILTER_PASSWORD%22%3A%22%24%7Binput%3Akilter-password%7D%22%7D%2C%22inputs%22%3A%5B%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22kilter-username%22%2C%22description%22%3A%22Kilter%20username%22%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22kilter-password%22%2C%22description%22%3A%22Kilter%20password%22%2C%22password%22%3Atrue%7D%5D%7D)

VS Code uses a `servers` key and can prompt for secrets once and store them securely via
`inputs`. Put this in `.vscode/mcp.json` or your user `mcp.json`
(command palette → **MCP: Open User Configuration**):

```json
{
  "inputs": [
    { "type": "promptString", "id": "kilter-username", "description": "Kilter username" },
    { "type": "promptString", "id": "kilter-password", "description": "Kilter password", "password": true }
  ],
  "servers": {
    "kilter": {
      "type": "stdio",
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${input:kilter-username}",
        "KILTER_PASSWORD": "${input:kilter-password}"
      }
    }
  }
}
```

Docs: <https://code.visualstudio.com/docs/copilot/customization/mcp-servers>

## Windsurf

Edit `~/.codeium/windsurf/mcp_config.json` (Settings → Cascade → MCP Servers → raw config).
Placeholder syntax is `${env:NAME}`:

```json
{
  "mcpServers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${env:KILTER_USERNAME}",
        "KILTER_PASSWORD": "${env:KILTER_PASSWORD}"
      }
    }
  }
}
```

Docs: <https://docs.windsurf.com/windsurf/cascade/mcp>

## Gemini CLI

```bash
gemini mcp add -s user -e KILTER_USERNAME="$KILTER_USERNAME" -e KILTER_PASSWORD="$KILTER_PASSWORD" kilter uvx kilter-mcp
```

Or in `~/.gemini/settings.json`. Gemini CLI expands `$VAR` / `${VAR}`. Note that it strips
inherited variables whose names contain `PASSWORD` unless they are listed explicitly in
`env`, so keep both entries:

```json
{
  "mcpServers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "${KILTER_USERNAME}",
        "KILTER_PASSWORD": "${KILTER_PASSWORD}"
      }
    }
  }
}
```

Docs: <https://geminicli.com/docs/tools/mcp-server/>

## Zed

Settings → AI → MCP Servers → Add Local Server, or in `settings.json`
(`~/.config/zed/settings.json` on macOS/Linux, `%APPDATA%\Zed\settings.json` on Windows):

```json
{
  "context_servers": {
    "kilter": {
      "command": "uvx",
      "args": ["kilter-mcp"],
      "env": {
        "KILTER_USERNAME": "your-kilter-username",
        "KILTER_PASSWORD": "your-kilter-password"
      }
    }
  }
}
```

Zed does not document environment-variable placeholders, so values are literal.
Docs: <https://zed.dev/docs/ai/mcp>

## Kiro CLI

Same files and format as the Kiro IDE (`~/.kiro/settings/mcp.json`), or:

```bash
kiro-cli mcp add --name kilter --scope global --command uvx --args kilter-mcp \
  --env KILTER_USERNAME="$KILTER_USERNAME" --env KILTER_PASSWORD="$KILTER_PASSWORD"
```

Docs: <https://kiro.dev/docs/mcp/configuration/>

---

## Something else?

If your client is not listed but supports local stdio MCP servers, use
`command: uvx`, `args: ["kilter-mcp"]`, and the two environment variables. Pull requests
adding verified instructions for other clients are welcome.
