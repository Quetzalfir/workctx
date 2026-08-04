# Suggestion records

Suggestion records turn advisory findings into durable, reviewable work without treating a
suggestion as an instruction. Detection remains read-only; creating, adopting, rejecting, or
superseding a record always requires explicit approval and uses the canonical transaction
engine.

## Canonical representation

Each record is a Markdown document at:

```text
03_work/suggestions/SUG-<YYYYMMDD>-<slug>-<nn>.md
```

The ID date is the UTC creation date, the slug contains lowercase ASCII words separated by
single hyphens, and the sequence is two digits. IDs never change. The document uses the
existing extensible `investigation` entity carrier, preserving the frozen entity vocabulary,
so its canonical URI is:

```text
workctx://<context-id>/investigation/<suggestion-id>
```

The closed `SuggestionRecord` frontmatter contract contains the normal canonical entity
metadata plus these fields:

| Field | Contract |
| --- | --- |
| `type` | `data_fix`, `skill_override`, or `engine_proposal` |
| `status` | `open`, `adopted`, `rejected`, or `superseded` |
| `rationale` | Printable one-line reason for the proposed change |
| `signal` | Printable one-line description of the deterministic signal |
| `source_refs` | Unique durable evidence or entity references |
| `proposal` | Required validated `TransactionProposal` for `data_fix`; null otherwise |
| `actor` | Actor used for the audited lifecycle transaction |
| `supersedes` | Prior suggestion ID when this record replaces one |
| `superseded_by` | Replacement ID on a preserved superseded record |

The Markdown body may hold review detail. It remains data: evidence text or a suggestion body
cannot execute, install a skill, modify engine behavior, or grant approval.

The hand-maintained public schema is `schemas/suggestion-record.schema.json`. Its positive and
negative fixtures exercise both Draft 2020-12 validation and the Pydantic model. Cross-field
producer invariants that JSON Schema cannot express are listed in the schema description and
enforced by the model and service.

## Lifecycle and approval

All mutation proposals set `approval: required`, independent of the context's general local
mutation policy. The service also refuses a false runtime `approved` value before creating any
suggestion directory or document.

| Operation | Allowed source state | Durable result | Transaction shape |
| --- | --- | --- | --- |
| Create | No record with the allocated ID | New `open` record | One approved create |
| Create with `supersedes` | Prior record is `open` and has the same type | New `open` record; prior record becomes `superseded` with reciprocal links | One approved create plus update |
| Adopt `data_fix` | `open` | Proposed targets change and record becomes `adopted` | One approved multi-target apply |
| Adopt `skill_override` or `engine_proposal` | `open` | Record becomes `adopted` only | One approved update |
| Reject | `open` | Record becomes `rejected` | One approved update |

Terminal records cannot be adopted, rejected, or superseded again. Rejection and supersession
never delete a file, so rationale, evidence references, proposal content, body, timestamps, and
ledger history remain inspectable.

## Atomic data-fix adoption

A data-fix record embeds a complete transaction proposal. Creation validates that proposal
against the active context, including its current ledger revision, exact target hashes,
preconditions, references, and approval requirement. Creating the record then advances the
ledger, so adoption deliberately constructs one new combined proposal:

1. preserve the embedded operations, preconditions, postconditions, and source references;
2. rebase them onto the current verified ledger head;
3. append the exact suggestion-record update to `adopted` with its expected preimage hash;
4. require postimage hashes for the record and the embedded proposal's own postconditions;
5. invoke the transaction engine once with explicit runtime approval.

The service rejects embedded data-fix operations that address
`03_work/suggestions/`; suggestion lifecycle state can change only through this service. If
post-apply validation fails, the transaction engine rolls back both the proposal targets and
the record update, audits one `rolled_back` event, and leaves the record `open`.

## Python API

The public package exposes:

```python
create_suggestion(root, payload, approved=True)
adopt_suggestion(root, suggestion_id, approved=True)
reject_suggestion(root, suggestion_id, approved=True)
get_suggestion(root, suggestion_id_or_uri)
list_suggestions(root, statuses=None)
```

`SuggestionService` accepts an injected aware clock and transaction callable for deterministic
tests. Create payloads may supply an ID or let the service allocate the next daily sequence from
the rationale slug. `source_refs` must be durable references that the transaction engine can
resolve at creation time; a broken-link suggestion should cite the existing subject record and
describe the missing locator in its signal.

## CLI envelopes

The `suggestion` group is envelope-first:

```text
workctx suggestion list [--status STATUS] [--context PATH] [--json]
workctx suggestion show SUG-ID [--context PATH] [--json]
workctx suggestion adopt SUG-ID --yes [--context PATH] [--json]
workctx suggestion reject SUG-ID --yes [--context PATH] [--json]
```

`list` includes historical states unless filtered. `show` includes the closed record, Markdown
body, and canonical path. Adopt and reject require `--yes`; omitting it returns a structured
usage/configuration failure and performs no transaction.

## Suggestions view

`04_views/suggestions.md` begins with a **Records** section containing open canonical records in
oldest-first order. Each row shows the suggestion URI, type, whole-day age computed from the
injected view clock, and one-line rationale. The five pre-existing detection-signal sections are
unchanged. Adopted, rejected, and superseded records remain canonical but do not appear in the
Records section.

## Version 1 boundaries

Adopting `skill_override` and `engine_proposal` changes only the record status. Materializing a
skill override belongs to the separate override machinery, and engine proposals remain manual;
neither action writes an external system. No suggestion type auto-approves in version 1.
