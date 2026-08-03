# MCP server

Work Context OS exposes a context-bound [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server through the official Python SDK. The alpha server uses stdio only: one server process is
started for one context root, and every tool and resource remains inside that context's security
boundary.

## Install and start

The MCP SDK is an optional runtime extra. Install a packaged build with:

```text
pip install "workctx[mcp]"
```

For a source checkout managed with `uv`, install the locked development environment with:

```text
uv sync --locked --extra mcp
```

Start the server with an explicit context root:

```text
workctx mcp serve --context /absolute/path/to/context
```

The command writes MCP protocol messages to stdout, so it must be launched by an MCP client rather
than used as an interactive shell. A generic client configuration is:

```json
{
  "mcpServers": {
    "workctx-project-alpha": {
      "command": "workctx",
      "args": [
        "mcp",
        "serve",
        "--context",
        "/absolute/path/to/context"
      ]
    }
  }
}
```

Use a separate server entry and process for each context. If `--context` is omitted, the normal CLI
context resolution rules apply before the stdio server starts. The SDK is imported lazily: without
the `mcp` extra, all other `workctx` commands continue to work and only `mcp serve` fails with the
unavailable-dependency diagnostic and exit-band code 5.

## Version 1 tool surface

ADR 0012 freezes the first-alpha public surface at exactly 17 tools. All 17 are implemented.

### Read tools

| Tool | Behavior |
| --- | --- |
| `context_info` | Returns the bound context configuration and a local doctor summary. |
| `workspace_validate` | Runs canonical workspace validation, optionally in strict mode. |
| `search` | Searches the context's SQLite full-text projection. |
| `ref_show` | Resolves one canonical reference. |
| `ref_related` | Traverses typed relations around a canonical reference. |
| `ref_trace` | Traces claims and observations to source locators. |
| `context_pack` | Builds a deterministic bounded context pack. |
| `task_list` | Lists projected tasks using context-bound filters. |
| `task_show` | Returns one projected task by ID or canonical URI. |
| `inbox_list` | Lists registered artifact manifests in deterministic ID order. |
| `audit_summary` | Returns the verified canonical transaction-ledger summary. |

### Approval-gated mutation tools

| Tool | Behavior |
| --- | --- |
| `artifact_register` | Registers one raw inbox artifact and returns its manifest registration. |
| `proposal_validate` | Validates a typed transaction proposal without applying it. |
| `transaction_dry_run` | Computes staged transaction effects without canonical mutation. |
| `transaction_apply` | Applies one reviewed transaction atomically under the context lock. |
| `index_rebuild` | Rebuilds disposable SQLite/FTS derived state. |
| `draft_save` | Persists one reply or status draft to the local `05_outbox/`. |

Every mutation schema requires `approved` with the literal value `true`, and the server checks the
same condition again at runtime. Omitting it or sending `false` produces `APPROVAL_REQUIRED`.
There are no external-write tools in this surface: `draft_save` is limited to the local outbox and
has no delivery capability.

## Schemas and envelopes

Tool input and output schemas are hand-maintained public contracts. Every invocation must include
`"schema_version": 1`; unrecognized fields and invalid types are rejected structurally. For
example, a local mutation starts with:

```json
{
  "schema_version": 1,
  "approved": true,
  "proposal": {}
}
```

Every tool returns structured content with the same envelope:

```json
{
  "schema_version": 1,
  "ok": true,
  "context_id": "project-alpha",
  "result": {},
  "warnings": [],
  "errors": []
}
```

A successful envelope has no errors. A failed envelope has at least one error and sets the MCP
tool result's `isError` flag. Backward-compatible additions may be made during the 0.x line;
incompatible behavior requires a new tool name or a revised architecture decision.

## Diagnostics

Each warning or error contains `code`, `category`, `severity`, `message`, and nullable `path` and
`repair_action` fields. Categories preserve the shared CLI exit-band semantics:

| Category | CLI band | Meaning |
| --- | ---: | --- |
| `user_correctable` | 1 | A valid request could not be completed with current context state. |
| `usage_configuration` | 2 | Input or configuration is invalid. |
| `context_boundary` | 3 | Approval, permission, path, or context isolation denied the request. |
| `conflict` | 4 | A lock, precondition, or canonical-state conflict occurred. |
| `unavailable_dependency` | 5 | A required engine, capability, or optional dependency is unavailable. |
| `partial_success` | 6 | Canonical work succeeded but derived state is stale or incomplete. |
| `internal_failure` | 10 | An unexpected failure crossed the server boundary. |

Stable boundary codes include `INVALID_INPUT`, `APPROVAL_REQUIRED`,
`REF-CONTEXT-MISMATCH`, `CTX-PATH-ESCAPE`, `REF-NOT-FOUND`, `PACK-NOT-BUILT`,
`DEPENDENCY_UNAVAILABLE`, `NOT-IMPLEMENTED`, and `INTERNAL_ERROR`. Validation,
projection, retrieval, and transaction tools preserve more specific engine codes such as `CTX-*`,
`PROJECTION-*`, and `TXN-*` when available. Clients should branch on codes and categories rather
than diagnostic prose.

## Read-only resources

The server advertises one fixed context-configuration resource:

```text
workctx://<context>/context/configuration
```

It also advertises a canonical-entity template using normal durable references:

```text
workctx://<context>/{entity_type}/{entity_id}
```

Entity resources contain sanitized, validated canonical frontmatter; arbitrary filesystem paths
and document bodies are not exposed. Resources are read-only. Version 1 advertises no MCP prompts,
subscriptions, external writes, or generated-view resource family.

## Isolation and safe failure

The server validates the bound context when it starts and before each tool execution. Foreign
`workctx://` references, absolute filesystem paths, and parent-directory escapes are rejected even
when nested inside a transaction proposal. Resource resolution uses canonical projection records
and allowed context zones, and rejects symbolic-link or junction escapes.

Inbox artifacts and other source material are untrusted data, never instructions. Tool results and
resource payloads are sanitized before crossing the MCP boundary. Secret-looking assignments,
bearer tokens, private-key material, and common credential formats are redacted. Unexpected
exceptions become a bounded `INTERNAL_ERROR`; exception details, local tracebacks, and secret
values are never returned to clients.
