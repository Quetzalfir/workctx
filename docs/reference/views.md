# Generated operational views and daily brief

Operational views are rebuildable Markdown projections under `04_views/`. They summarize
canonical tasks, task-subject claims, operational resources, processed artifacts, people
referenced by waiting-on fields, and verified audit activity. They are never canonical input and
must not be edited as a competing source of truth.

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

For the same projected entity/task/claim state, canonical resource and artifact metadata,
verified ledger state, stale threshold, and generation timestamp, rendering is byte-identical.
Rebuilds use LF newlines and UTF-8 without a BOM.

## Files

| Path | Contents |
| --- | --- |
| `04_views/current-focus.md` | Ready, active, blocked, and waiting tasks ordered by priority, due timestamp, and task ID. |
| `04_views/next-actions.md` | The next action for each actionable task in the same deterministic order. |
| `04_views/waiting-on.md` | Tasks grouped by waiting-on value; local person URIs are resolved through the retrieval API to display names. |
| `04_views/stale-knowledge.md` | Current task-subject claims whose `observed_at` age meets the configured stale threshold. |
| `04_views/brief.md` | Human-readable rendering of the structured daily-brief payload. |
| `04_views/resource-directory.md` | System, service, and integration entities grouped by public, SSO, VPN, other, and ungrouped access. |
| `04_views/status-report.md` | Factual seven-day task, commitment, blocker/waiting, and processed-evidence activity suitable for a manager update. |

The resource directory reads the eligible entity set from `SQLiteProjection.query_entities`, then
loads each source document through `CanonicalStore`. Typed `references` are ungrouped links. The
optional `access_urls` extra frontmatter field accepts URL strings or mappings with `url`, optional
`label`, and optional `access`. Recognized access values are `public`, `sso`, `vpn`, and `other`;
other non-empty values join the `other` group, while missing access values are ungrouped. An
explicit `access_urls` entry supplies the access group for a duplicate typed-reference target.
Entities without links still appear in the ungrouped table. Within each group, resources are
ordered by case-insensitive title and stable ID, and the ungrouped table is last.

Every resource-directory line is checked with `contains_possible_secret` before emission. A
flagged entity row is omitted. The view adds one `excluded: possible secret` note per affected
entity, and that note identifies only the entity ID; the rejected title, description, URL, and URI
are not copied into the note.

The status-report period is the inclusive interval from seven days before the injected generation
clock through the generation timestamp. Its sections are:

- **Completed:** currently done task records whose current status claim began in the period, with
  task `updated_at` as the fallback for claim-free authored tasks;
- **Moved:** status claims created by committed audit events in the period and linked to their
  superseded status claims;
- **Blocked and waiting:** currently blocked or waiting tasks, with age measured from the current
  status claim and task `updated_at` as the fallback for claim-free authored tasks;
- **New commitments:** tasks created in the period that carry a due timestamp;
- **Evidence processed:** currently processed artifact manifests whose archive update audit event
  falls in the period.

Each rendered item carries its task `workctx://` URI or artifact `artifact://` URI. Empty sections
use an explicit factual `_No ..._` message. The renderer does not infer narrative, completion
percentages, or unstored progress.

The SQLite API exposes claim history by subject rather than a global claim scan. Accordingly, the
stale-knowledge and status-report views inspect claims attached to projected tasks, which is the
views engine's operational scope. The views engine never issues raw SQL. Entity, task, and claim
data comes from typed `SQLiteProjection` queries; waiting-on identity resolution uses
`workctx.retrieval.resolve`; resource documents use `CanonicalStore`; and processed artifacts use
the ingestion listing API. Verified ledger summary APIs supply the source revision and brief
metadata, while `read_audit_events` supplies chain-verified chronological events for the status
report.

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
