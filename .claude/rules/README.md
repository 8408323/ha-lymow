# Rules

Rules are modular instruction files that Claude loads automatically. They extend `CLAUDE.md` without bloating it.

- `alwaysApply: true`. Loaded every session, regardless of what files are open. Costs tokens every turn, so keep it tight.
- `paths: [...]`. Loaded only when working with files matching the glob patterns. Free until you're near matched files.

Budget convention for `alwaysApply` rules: under 30 lines each. Push everything that doesn't actively change Claude's behavior into a path-scoped rule, into an agent, or out entirely.

## Available rules

### code-quality.md
**Scope**: Always. ~22 lines.

Anti-defaults that counter common Claude tendencies (no premature abstraction, no scope expansion, no surrounding refactors, WHY-not-WHAT comments). Plus naming conventions, code markers (TODO, FIXME, HACK, NOTE), and file organization.

### testing.md
**Scope**: Always. ~12 lines.

Terse testing principles: verify behavior, run the specific test file, fix or delete flaky tests, prefer real implementations, no empty assertions. Comprehensive test writing is handled by the `test-writer` skill.

### security.md
**Scope**: Path-scoped (`custom_components/lymow/**`, `scripts/**`, `tools/**`)

Loads when touching integration or tooling code. This integration is a cloud client, not a server: untrusted-input validation on decoded protobuf / MQTT / REST payloads, never logging tokens / PIN / GPS / PII, keeping credentials out of source, constant-time secret comparison, and treating capture artifacts as live secrets.

### error-handling.md
**Scope**: Path-scoped (`custom_components/lymow/**`)

Loads when touching integration code. Home Assistant error conventions: the right typed exception (`ConfigEntryAuthFailed` / `ConfigEntryNotReady` / `UpdateFailed` / `HomeAssistantError`), never swallow, distinguish expected failures from bugs, retry only transient failures with backoff, no token leaks in messages, no fire-and-forget coroutines.

(No `frontend.md` / `database.md` — this is a pure-Python integration with no web UI or database. They were dropped when porting the framework from equibeam.)

## Adding your own

Create a new `.md` file in this directory:

```yaml
---
alwaysApply: true
---

# Your Rule Name

- Your instructions here
```

Or path-scoped:

```yaml
---
paths:
  - "src/your-area/**"
---

# Your Rule Name

- Instructions that only apply when touching these files
```

See [Claude Code docs](https://code.claude.com/docs/en/memory#path-specific-rules) for glob pattern syntax.
