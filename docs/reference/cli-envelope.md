# CLI result envelope

Work Context OS uses one presentation boundary for machine-readable command results. After
Typer has successfully parsed a command that accepts `--json`, stdout contains exactly one
JSON object and no decorative text. Human diagnostics are written to stderr.

`workctx version` deliberately remains plain text. The current JSON-capable command table is:

| CLI command | Envelope `command` | Primary `result` fields |
| --- | --- | --- |
| `doctor` | `doctor` | `checks` |
| `context init` | `context.init` | `root`, `context` |
| `context register` | `context.register` | `id`, `path`, `active` |
| `context list` | `context.list` | `count`, `contexts` |
| `context unregister` | `context.unregister` | `id`, `removed` |
| `context inspect` | `context.inspect` | `root`, `context` |
| `context validate` | `context.validate` | `root`, `issues` |
| `validate` | `context.validate` | `root`, `issues` |
| `inbox add` | `inbox.add` | `count`, `outcomes` |
| `inbox list` | `inbox.list` | `count`, `artifacts` |
| `artifact show` | `artifact.show` | `artifact` |
| `artifact verify` | `artifact.verify` | `verification` |
| `brief` | `brief` | `schema_version`, `context_id`, `generated_at`, `source_revision`, `today_focus`, `blockers`, `waiting_on`, `stale_claims`, `recent_ledger_activity` |
| `view rebuild` | `view.rebuild` | `schema_version`, `context_id`, `generated_at`, `source_revision`, `views` |
| `index rebuild` | `index.rebuild` | `root`, `trigger`, `counts`, `skipped` |
| `ref show` | `ref.show` | `resolution` |
| `ref related` | `ref.related` | `focal`, `depth`, `direction`, `nodes`, `edges` |
| `ref trace` | `ref.trace` | `focal`, `claims`, `observations`, `missing_observations` |
| `context-pack` | `context-pack` | `pack` |
| `proposal validate` | `proposal.validate` | `validation` |
| `proposal show` | `proposal.show` | `dry_run` |
| `transaction apply` | `transaction.apply` | `dry_run`, then `preview` or `receipt` |
| `transaction history` | `transaction.history` | `summary`, `events` |
| `transaction show` | `transaction.show` | `event` |
| `search` | `search` | `query`, `count`, `hits` |
| `task list` | `task.list` | `count`, `tasks` |
| `task show` | `task.show` | `task` |
| `suggestion list` | `suggestion.list` | `count`, `suggestions` |
| `suggestion show` | `suggestion.show` | `suggestion` |
| `suggestion adopt` | `suggestion.adopt` | `operation`, `suggestion`, `receipt` |
| `suggestion reject` | `suggestion.reject` | `operation`, `suggestion`, `receipt` |
| `usage status` | `usage.status` | `enabled`, `path`, `file_size_bytes`, `rotated_file_count`, `rotated_size_bytes`, `summary` |
| `usage evaluate` | `usage.evaluate` | `count`, `candidates` |
| `usage suggest` | `usage.suggest` | `candidate_count`, `created_count`, `skipped_count`, `created`, `skipped` |
| `agent detect` | `agent.detect` | `clients` |
| `agent status` | `agent.status` | `statuses` (including `merge_candidates`) |
| `agent install` | `agent.install` | `applied`, `plans` (including `merge_candidates` and `adopts_trust`), `receipts` |
| `agent repair` | `agent.repair` | `applied`, `plans`, `receipts` |
| `agent uninstall` | `agent.uninstall` | `applied`, `plans`, `receipts` |
| `agent forget` | `agent.forget` | `root`, `removed`, `adapters`, `install_treatment`, `message` |
| `agent open` | `agent.open` | `session` |
| `migrate legacy` | `migrate.legacy` | `mode`, `applied`, `report` |
| `secret set` | `secret.set` | `name`, `stored`, `backend` |
| `secret check` | `secret.check` | `name`, `resolvable`, `layer` |
| `secret list` | `secret.list` | `count`, `secrets`, `os_store_available` |
| `secret unset` | `secret.unset` | `name`, `deleted`, `environment_present` |
| `secret import` | `secret.import` | `count`, `names`, `source_deleted` |
| `connector list` | `connector.list` | `count`, `connectors` |
| `connector sync` | `connector.sync` | named: `connector_name`, `snapshots`, `duration_ms`; batch: `outcomes`, `duration_ms` |
| `connector status` | `connector.status` | `count`, `checked_at`, `snapshots` |
| `outbox send` | `outbox.send` | preview: `operation`, `draft_id`, `channel`, `target`, `recipient_display`, `body`, `draft_content_hash`, `fingerprint`; sent: `operation`, `draft`, `delivery`, `receipt` |

`transaction apply` is a dry run unless `--yes` is present; `--dry-run` wins when both flags
are supplied. A preview reports `dry_run: true` and never applies the proposal. An approved
apply reports `dry_run: false` and includes the authenticated transaction receipt. Likewise,
`agent install` returns the complete plan without changing files unless `--yes` is present.
`suggestion adopt` and `suggestion reject` never preview or mutate without `--yes`; omission
returns an envelope-first usage/configuration failure with exit code 2.
`usage suggest` likewise requires `--yes`; evaluation alone is always read-only, and each created
record is committed through the approved suggestion transaction API.
`outbox send` previews by default. JSON execution requires both `--yes` and the exact
`--fingerprint` returned by preview; omission is an envelope-first usage failure, while a stale
draft or swapped target is a conflict. Human `--yes` renders a new full preview and confirms it
interactively before the fingerprint-pinned send. Delivery failure envelopes contain only safe
operation metadata and a content-free diagnostic.

## Envelope contract

Every JSON result has all of these fields:

```json
{
  "ok": true,
  "command": "context.validate",
  "context_id": "example-context",
  "result": {
    "root": "/resolved/context",
    "issues": []
  },
  "warnings": [],
  "errors": [],
  "meta": {
    "schema_version": 1,
    "duration_ms": 3
  }
}
```

- `result` is always an object. In particular, `doctor` returns
  `{"checks": [...]}` rather than a top-level list.
- `context_id` is `null` for commands without a context and when resolution fails before
  an ID is known.
- warnings and errors are objects with `code`, `message`, and an optional `path`.
- successful envelopes have no errors; failed envelopes contain at least one error.
- diagnostic messages are sanitized, bounded, and single-line. Unexpected failures expose
  a stable generic message instead of exception details or tracebacks.
- `schema_version` is the envelope contract version. `duration_ms` is non-negative wall
  duration measured with a monotonic clock.

The canonical hand-maintained schema is `schemas/cli-envelope.schema.json`. Positive and
negative fixtures validate both that schema and the Pydantic presentation model, following
ADR 0008.

## Streams and framework errors

For a parsed JSON command, the envelope is the only stdout document on success or runtime
failure. A concise diagnostic is also written to stderr on failure. Human tables and status
messages use stdout; human failure diagnostics use stderr.

Typer owns failures that occur before a command can be parsed, such as an unknown option or
a missing required argument. Those retain Typer's native stderr usage text, empty stdout,
and exit code 2. They do not enter the command envelope boundary because no JSON command has
yet been established.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Validation or another user-correctable failure |
| 2 | Usage or configuration failure; Typer parsing keeps its native behavior |
| 3 | Context boundary or permission denial |
| 4 | Conflict or stale precondition |
| 5 | Required dependency or plugin unavailable |
| 6 | Partial success with stale derived state |
| 10 | Unexpected internal failure |

Codes 3, 4, and 6 are mapped at the shared boundary for later command families even though
the current commands do not produce them. A failed required `doctor` check returns 5. An
attempt to initialize a non-empty directory returns 1.

## Context resolution

Context-aware commands accept the same command-level `--context PATH` option while retaining
their optional positional path for compatibility. Resolution order is:

1. an explicit `--context PATH` or positional path, with `--context` winning when both are
   present;
2. the nearest ancestor of the current directory containing `context.yaml`;
3. a clear context-not-found failure.

An invalid explicit path never falls back to the current directory. Falling back to the
active context in the user-level registry, between ancestor discovery and the final
failure, is an intentional seam reserved for a future release; today the resolver does
not read the registry.

`context init` resolves and prints its creation target in human mode and returns the same
absolute target under `result.root` in JSON mode.

## Validate alias

`workctx validate` is a stable public alias for `workctx context validate`. Both forms
return the canonical command identifier `context.validate` and use the same result and
exit behavior.
