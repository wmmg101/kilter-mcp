# Product

kilter-mcp is an MCP server, not an application. The whole product is:

```
user adds MCP to Kiro → provides KILTER_USERNAME/KILTER_PASSWORD → agent gets Kilter tools
```

- Users never run the binary directly; Kiro launches `uvx kilter-mcp`.
- Users never call tools by name; they ask the agent natural questions.
- Do not add dashboards, login commands, keyring, token files, databases, or UIs.
- Every tool is read-only. Statistics tools see only the authenticated user's own Kilter
  logbook; `kilter_plan_session` additionally reads Kilter's curated climb list (shared,
  public catalogue data) so it can suggest climbs the user has never logged. No other
  users' data, ever.
- Keep v0.1 small. New tools must answer a concrete user question
  ("compare 20° and 30°", "my hardest sends") and have understood semantics.
- Unofficial project: never describe Kilter endpoints as an official public API.
