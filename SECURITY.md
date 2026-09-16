# Security

kilter-mcp asks for your Kilter username and password, so its whole value depends on
handling them carefully. This page explains what it does with them and how to report a
problem.

## What the server does with your credentials

- The username and password are read from the `KILTER_USERNAME` and `KILTER_PASSWORD`
  environment variables that your MCP client passes to the process. Nothing else reads them.
- They are used for exactly one purpose: a password-grant request to Kilter's login server
  (`idp.kiltergrips.com`, Keycloak realm `kilter`) to obtain an access token and a refresh
  token.
- Tokens are held in process memory only, for the lifetime of the MCP process. They are never
  written to disk, a keychain, or any cache.
- The server talks to two hosts only: `idp.kiltergrips.com` (login and refresh) and
  `portal.kiltergrips.com` (your logbook and the public grade table). There is no telemetry
  and no third-party endpoint.
- Every tool is read-only. The server never writes to your Kilter account.
- Credentials, tokens and `Authorization` headers are never included in tool output, log
  lines or error messages. Error text from the network layer passes through a redaction
  step, and the code is structured so a Python traceback's failing frame does not hold the
  password (tests enforce both).

- After a rejected login the server waits 60 seconds before trying again, however many
  tools the agent calls in between. A mistyped password therefore cannot turn into a burst of
  failed logins that trips Kilter's account-lockout protection.
- `kilter-mcp --check` exists so you can troubleshoot without pasting configs around: it
  prints counts and a masked username only.

## What it cannot protect you from

- Your MCP client's configuration file. If you put the password directly in `mcp.json`
  (rather than an environment-variable placeholder), anyone who can read that file can read
  the password. Keep such files out of version control and prefer the `${VAR}` form where
  your client supports it; see [docs/clients.md](docs/clients.md).
- The process environment. MCP clients pass credentials to local servers as environment
  variables, which other processes running as the same OS user can read (for example with
  `ps` on macOS/Linux). This is how every stdio MCP server receives configuration; there is no
  more private channel available to us.
- Full-account access. Kilter does not offer scoped or read-only tokens, so the token this
  server obtains could in principle do anything your account can. The server only ever
  issues read requests, and the code that talks to Kilter is small enough to audit
  (`src/kilter_mcp/client.py`, `auth.py`, `endpoints.py`).
- The AI model you are talking to. Tool output (your climbs, grades, dates) is sent to
  whatever model your client uses. Credentials are not, but your climbing history is.
- Kilter's own systems and terms. This is an unofficial client; see the disclaimer in the
  README.

## Supported versions

Only the latest release on PyPI receives fixes. Upgrade with `uvx --refresh kilter-mcp`
(or `pip install -U kilter-mcp`).

## Reporting a vulnerability

If you find a way for credentials or tokens to leak, or any other security problem, please
do not open a public issue. Instead use GitHub's private reporting:

**https://github.com/wmmg101/kilter-mcp/security/advisories/new**

Include what you found, how to reproduce it, and the version (`kilter-mcp --version`). You
should get an acknowledgement within a few days. Once a fix is released the advisory is
published with credit to you, unless you prefer otherwise.

Issues that are not security problems (a tool returns wrong numbers, a client config
snippet is outdated) belong in the normal issue tracker.
