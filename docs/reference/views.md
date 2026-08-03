# Generated operational views and daily brief

Operational views are rebuildable Markdown projections under `04_views/`. They summarize
canonical tasks, task-subject claims, people referenced by waiting-on fields, and verified audit
metadata. They are never canonical input and must not be edited as a competing source of truth.

## Public API

`workctx.views` exports:

- `brief(context_root, *, clock=..., stale_after=...)`, which returns a typed `BriefPayload`
  without writing a view file;
- `rebuild_views(context_root, *, clock=..., stale_after=..., session_id=...)`, which rebuilds
  all operational views from one snapshot;
- `rebuild_view(context_root, name, ...)`, which rebuilds one `ViewName`;
- `ViewService` for callers that need a shared context-bound projection, injected clock, or
  repeated rebuilds.

The default stale threshold is 30 days. Callers may inject another positive `timedelta`. Public
service helpers have a UTC wall-clock default for interactive callers, while snapshot assembly
and rendering always receive an explicit timezone-aware timestamp. Tests and automation should
inject a clock.

## Generated-header contract

Every generated file starts with this minimal YAML frontmatter, in this exact field order:

```yaml
---
generated_by: workctx.views
source_revision: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
generated_at: 2026-08-02T18:00:00Z
---
```

`source_revision` is the verified `event_hash` at the audit-ledger head. An empty ledger uses the
64-zero genesis revision. `generated_at` is the caller-supplied or injected-clock timestamp,
normalized to UTC whole seconds. `generated_by` distinguishes derived files from authored
Markdown. Workspace validation excludes the complete `04_views` zone from canonical-document
validation; the header is still present so humans and downstream tools can identify provenance.

For the same projected task/claim state, verified ledger state, stale threshold, and generation
timestamp, rendering is byte-identical. Rebuilds use LF newlines and UTF-8 without a BOM.

## Files

| Path | Contents |
| --- | --- |
| `04_views/current-focus.md` | Ready, active, blocked, and waiting tasks ordered by priority, due timestamp, and task ID. |
| `04_views/next-actions.md` | The next action for each actionable task in the same deterministic order. |
| `04_views/waiting-on.md` | Tasks grouped by waiting-on value; local person URIs are resolved through the retrieval API to display names. |
| `04_views/stale-knowledge.md` | Current task-subject claims whose `observed_at` age meets the configured stale threshold. |
| `04_views/brief.md` | Human-readable rendering of the structured daily-brief payload. |

The Phase 1 frozen SQLite API exposes claim history by subject rather than a global claim scan.
Accordingly, the stale-knowledge view covers claims attached to projected tasks, which is the
WP-400 operational scope. It never scans canonical Markdown or issues raw SQL. Task and claim
data comes from typed `SQLiteProjection` queries, and waiting-on identity resolution uses
`workctx.retrieval.resolve`. Verified ledger summary APIs supply only the revision and recent
activity metadata.

View writes go directly to `04_views` under the context lock and use an atomic single-file
replacement. They do not create transaction proposals or audit events because views are derived
state. A rebuild does not change its own `source_revision`.

## Structured brief payload

`BriefPayload` is a frozen Pydantic record with this JSON-facing shape:

```json
{
  "schema_version": 1,
  "context_id": "fictional-project",
  "generated_at": "2026-08-02T18:00:00Z",
  "source_revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "today_focus": [],
  "blockers": [],
  "waiting_on": [],
  "stale_claims": [],
  "recent_ledger_activity": {
    "event_count": 0,
    "head_revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "last_event_id": null,
    "last_proposal_id": null,
    "last_timestamp": null
  }
}
```

`today_focus` and `blockers` contain `TaskViewItem` records with task identity, URI, title,
priority, status, owner, due timestamp, next action, blockers, and waiting-on values.
`waiting_on` contains a value, display name, optional resolved person URI, and ordered task
records. `stale_claims` contains claim identity, URI, subject, predicate, JSON value,
`observed_at`, and integer age in days. `recent_ledger_activity` is a verified compact summary;
it does not expose audit payload content.

A read-only brief verifies the ledger before and after projection reads and raises
`ViewSourceChangedError` if the head advances during assembly. A view rebuild holds the context
lock while it takes its snapshot and writes the selected derived files.
