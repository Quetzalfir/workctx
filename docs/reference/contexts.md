# Context lifecycle and machine inventory

A context is an isolated directory whose canonical identity and policy live in `context.yaml`.
The context file remains the source of truth. The per-user registry is advisory, machine-local
state used for discovery; it is not synchronized into a context and can be rebuilt by explicitly
registering context roots.

## Commands

```text
workctx context init PATH --name NAME [--id ID] [--kind KIND] [--profile PROFILE]
workctx context register [PATH] [--json]
workctx context list [--json]
workctx context unregister CONTEXT-ID [--json]
workctx context inspect [PATH] [--context PATH] [--json]
workctx context validate [PATH] [--context PATH] [--strict] [--json]
```

`context init` creates the versioned workspace and then best-effort registers its resolved root.
Registry state is advisory, so a registry permission, corruption, or filesystem failure emits a
warning but never turns successful context creation into a failure.

`context register` resolves `PATH`, or resolves the current/active context when it is omitted,
reads the canonical ID from `context.yaml`, and stores that ID and absolute root. Registration is
idempotent. Registering the same ID at a different valid root updates the machine-local binding.
It does not modify the context.

`context unregister` removes only the named registry entry and is idempotent. It never deletes or
changes the registered directory, including when the registry entry is stale.

`context inspect` reports the resolved canonical configuration. `context validate` runs the
workspace validation rules; the top-level `workctx validate` command remains its alias.

## Inventory semantics

`context list` reads the registry without taking its mutation guard. For each registration it
reads `context.yaml` and reports the registered ID, configured ID, name, kind, profile, user
interaction language, absolute path, active flag, health flags, and cheap statistics:

- `tasks`: Markdown files below `03_work/tasks/`;
- `entities`: Markdown entity files below `02_knowledge/`, including evidence notes;
- `evidence_notes`: Markdown files below `02_knowledge/evidence/`;
- `pending_inbox_artifacts`: valid manifests under `00_inbox/manifests/` whose status is
  `pending`;
- `ledger_events` and `last_ledger_activity`: the count and final timestamp from the verified
  canonical audit summary.

README files, hidden files, and linked descendants are not counted. Missing canonical directories
count as zero; a canonical inventory path that exists but is not a regular directory makes that
row unreadable. Inventory never opens SQLite, rebuilds a projection, acquires the context write
lock, repairs a context, or removes a stale registration.

An entry whose `context.yaml` no longer exists has `missing: true`. An entry whose configured ID
differs from its registered ID has `mismatched: true` and exposes the current value as
`configured_id`. Invalid configuration, unreadable canonical files, or an invalid audit ledger
leave the entry visible with an `error` string and `stats: null`; one bad context does not abort
the rest of the inventory.

If the registry itself cannot be read, `context list` returns a successful empty inventory plus
the `CONTEXT_REGISTRY_UNAVAILABLE` warning. This preserves the advisory boundary while making the
failure visible. Human output is one aligned table; JSON output uses the standard CLI envelope:

```json
{
  "ok": true,
  "command": "context.list",
  "context_id": null,
  "result": {
    "count": 1,
    "contexts": [
      {
        "id": "fictional-project",
        "configured_id": "fictional-project",
        "name": "Fictional Project",
        "kind": "project",
        "profile": "hybrid",
        "language": "en",
        "path": "D:\\WorkContexts\\fictional-project",
        "active": false,
        "missing": false,
        "mismatched": false,
        "stats": {
          "tasks": 2,
          "entities": 4,
          "evidence_notes": 1,
          "pending_inbox_artifacts": 1,
          "ledger_events": 3,
          "last_ledger_activity": "2026-08-05T12:00:00Z"
        },
        "error": null
      }
    ]
  },
  "warnings": [],
  "errors": [],
  "meta": {
    "schema_version": 1,
    "duration_ms": 2
  }
}
```
