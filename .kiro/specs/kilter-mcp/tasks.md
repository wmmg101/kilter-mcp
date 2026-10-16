# kilter-mcp — Tasks

- [x] 1. Verify MCP Python SDK (mcp 2.x, `MCPServer`) and Kiro `mcp.json` syntax
- [x] 2. Research grade mapping (`GET /api/grades`, public, 39 rows) and log semantics
- [x] 3. Scaffold package: pyproject, git, .gitignore, .kiroignore, LICENSE
- [x] 4. `config.py` — Settings from env, redacted repr, `redact()`
- [x] 5. `endpoints.py` — centralised URLs
- [x] 6. `auth.py` — password grant, in-memory tokens, refresh, rotation, fallback
- [x] 7. `models.py` — LogEntry, Grade, tolerant parsing
- [x] 8. `client.py` — KilterClient.get_logs / get_grades (+401 retry, grade fallback)
- [x] 9. `grades.py` — GradeTable + embedded fallback table
- [x] 10. `analytics.py` — filters, sends, projects, summary, pyramid, hardest, sessions,
      progression, angle stats
- [x] 11. `server.py` — MCPServer, 9 read-only tools, `main()` with --help/--version
- [x] 12. Tests: config, auth (success/failure/refresh/rotation), client, analytics,
      server tools in-process, redaction (56 tests)
- [x] 13. README (user-first, Add-to-Kiro button), CONTRIBUTING.md, LICENSE, CI workflow
- [x] 14. Verify: wheel via `uvx --from dist/…`, stdio handshake, ruff, pytest
- [x] 15. Security audit of tree (no secrets, UUIDs, emails)
- [x] 16. Test from Kiro with real credentials (Kiro + integration test both pass)
- [x] 17. GitHub repo wmmg101/kilter-mcp public; 0.1.0 on PyPI via trusted publishing
- [x] 18. 0.1.1: 60 s logbook cache, Retry-After handling, SECURITY.md, CHANGELOG
- [x] 19. Timezone-aware sessions (`KILTER_TIMEZONE` or host tz) — 0.2.0
- [x] 20. Surface user's own grade/rating from embedded `climbRating` — 0.2.0
- [x] 21. Published 0.2.0 to MCP Registry as io.github.wmmg101/kilter-mcp
- [ ] 22. (later) climb catalogue tools beyond session planning (name search, climb lookup)
- [ ] 23. (later) `.mcpb` bundle for Claude Desktop
- [x] 24. 0.2.1: login-failure cooldown, --check, Windows/macOS CI, Dependabot, issue
      templates, CoC, branch ruleset (ci check required, no force-push/delete on main)
- [x] 25. 0.2.2: label + grade_note for collapsed V-grades, logbook completeness warning,
      total_entries/timezone envelope on every response, list defaults 25
- [x] 26. `kilter_plan_session`: warm-up/main/projects playlist for an upcoming session at
      an angle and target grade, from the user's own logged climbs; `GradeTable.resolve`
- [x] 27. Session playlist tops up with climbs new to the user from `/api/climbs/curated`,
      wall inferred from `/api/climbs/logged` edge boxes (`catalogue.py`); probed and
      documented the portal catalogue endpoints (sizes, filters, 405s) in design.md
- [x] 28. Repeats at a new angle: sends from other angles graded at the target angle via
      `/api/climb-stat/{uuid}` (bounded, cached), preferred over new climbs in top-ups
