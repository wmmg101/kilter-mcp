# Security

Credentials and tokens must never appear in: tool output, logs, exception messages,
README examples, tests, fixtures, git history, CI, debug dumps.

- Never log or return passwords, access tokens, refresh tokens or `Authorization` headers.
- `Settings.__repr__` redacts the password; never add `__repr__`/`__str__` that exposes
  token state.
- Wrap external errors with `redact()` before surfacing them to the agent.
- Tokens live only in process memory. No token files, no keyring, no persistence.
- Tests use synthetic data only: fake UUIDs like `test-climb-1`, fake users
  `test-user`, fake tokens like `test-access-token`. No real emails, UUIDs or dumps.
- Real-account integration tests are opt-in via `KILTER_INTEGRATION=1`, skipped in CI,
  and must not print log entries.
- `.gitignore` excludes `.env*`, `*.token*`, `dumps/`, `.venv`, caches.
- Before any public push: `git diff`, grep for `Bearer`, `password=`, `@`-emails,
  real-looking UUIDs; run tests.
- Never push to a remote unless the user explicitly asks.
