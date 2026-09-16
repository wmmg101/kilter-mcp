# Product

kilter-mcp is an MCP server, not an application. The whole product is:

```
user adds MCP to Kiro → provides KILTER_USERNAME/KILTER_PASSWORD → agent gets Kilter tools
```

- Users never run the binary directly; Kiro launches `uvx kilter-mcp`.
- Users never call tools by name; they ask the agent natural questions.
- Do not add dashboards, login commands, keyring, token files, databases, or UIs.
- Every tool is read-only over the authenticated user's own Kilter logbook.
- Keep v0.1 small. New tools must answer a concrete user question
  ("compare 20° and 30°", "my hardest sends") and have understood semantics.
- Unofficial project: never describe Kilter endpoints as an official public API.
