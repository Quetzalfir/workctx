# CLI specification

## Design goals

- useful to a human in a terminal;
- deterministic and scriptable;
- consistent human and JSON output;
- safe defaults and explicit mutation;
- context-aware without hidden cross-context behavior;
- Windows-first quality without reducing macOS or Linux support.

## Command families

### Installation and health

```text
workctx version
workctx doctor [--json]
workctx completion install
```

### Context lifecycle

```text
workctx context init <path> --name <name> [--id <id>] [--profile <profile>]
workctx context list [--json]
workctx context inspect [<path>] [--json]
workctx context validate [<path>] [--strict] [--json]
workctx context migrate [<path>] [--dry-run]
workctx context backup [<path>] --output <archive>
```

### Inbox and artifacts

```text
workctx inbox add <files...> [--source <source>] [--event-date <date>]
workctx inbox list [--status pending]
workctx artifact show <artifact-id-or-uri>
workctx artifact verify <artifact-id-or-uri>
```

### Proposal and transaction

```text
workctx proposal validate <proposal-file>
workctx proposal show <proposal-file>
workctx transaction apply <proposal-file> [--dry-run] [--yes]
workctx transaction history [--limit 20]
workctx transaction show <transaction-id>
```

### Retrieval

```text
workctx search <query> [--type <type>] [--limit <n>] [--json]
workctx ref show <uri> [--json]
workctx ref related <uri> [--depth <n>] [--relation <type>] [--json]
workctx ref trace <uri> --to-source [--json]
workctx context-pack <uri-or-query> [--budget <tokens>] [--json]
```

### Work and operational views

```text
workctx task list [--status active] [--waiting-on <person>]
workctx task show <task-id>
workctx brief [--today] [--json]
workctx view rebuild [--only <view>]
workctx index rebuild
```

### Agent integration

```text
workctx agent detect
workctx agent install --agent codex|claude|gemini|all [--scope project]
workctx agent status
workctx agent open [<context>] --agent <agent>
workctx mcp serve [--context <path>]
```

### Development leadership

```text
workctx dev work-order create <id> --from <work-package>
workctx dev work-order validate <directory>
workctx dev report validate <report.json>
```

## Phase 1 alpha minimum

The first integrated alpha must implement:

- `version`;
- `doctor`;
- `context init`;
- `context inspect`;
- `context validate`;
- `inbox add` and `inbox list`;
- proposal validation;
- transaction dry-run and apply;
- search;
- reference show/related/trace;
- context pack;
- task list/show;
- brief;
- index/view rebuild;
- agent detect/install/status/open;
- MCP serve.

## Context resolution

Resolution precedence:

1. explicit `--context` or path;
2. nearest ancestor containing `context.yaml`;
3. active context selected in user-level registry;
4. fail with a clear error.

Commands must print the resolved context for mutation unless `--quiet` is used.

## Output contract

Human output uses Rich but remains readable without color. `--json` returns one JSON object with:

```json
{
  "ok": true,
  "command": "context.validate",
  "context_id": "new-company",
  "result": {},
  "warnings": [],
  "errors": [],
  "meta": {
    "schema_version": 1,
    "duration_ms": 23
  }
}
```

JSON output must not contain decorative text on stdout. Diagnostics go to stderr when appropriate.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | validation or user-correctable error |
| 2 | usage/configuration error |
| 3 | context boundary or permission denial |
| 4 | conflict or stale transaction precondition |
| 5 | unavailable dependency or plugin |
| 6 | partial success with stale derived state |
| 10+ | unexpected internal failure |

## Mutation UX

- default to dry-run or explicit confirmation for destructive or broad operations;
- support `--yes` only for local canonical writes whose policy allows it;
- external writes remain a separate approval boundary;
- display affected canonical entities and references before apply;
- provide a transaction ID and recovery instructions after apply.

## Configuration

- user registry under platform-appropriate config directories;
- context configuration in `context.yaml`;
- generated agent configuration in agent-native project directories;
- secret values resolved from keyring or external providers;
- environment variables may override only documented non-secret or secret-reference settings;
- no silent use of another context's settings.
