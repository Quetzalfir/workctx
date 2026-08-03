# Drafting and the local outbox

Drafting is a deterministic local workflow around canonical Markdown documents in
`05_outbox/`. A saved document has `entity_type: draft`, `status: draft`, and
`delivery_state: unsent`. Saving a draft is never evidence that a message was sent or a
document was published.

## Public API

`workctx.drafting` exports:

- `gather_reply_context(root, person_uri, *, task_uri=None)`, which returns a bounded context
  pack, all projected claims about the person, tasks waiting on that person, the selected task
  when supplied, and verified recent ledger metadata;
- `save_draft(root, payload, *, approved)`, which creates or revises one canonical draft only
  through `workctx.transactions.apply`;
- `list_drafts(root)` and `get_draft(root, draft)`, which read validated canonical outbox
  documents. `get_draft` accepts a draft ID or a local canonical draft URI.

Context gathering uses only typed retrieval, projection, task-query, and audit APIs. When a
task URI is supplied, its task is the context-pack focal entity; otherwise the person is the
focal entity. The operation includes complete person-claim history and uses a stable task-ID
order. It contains no LLM, network, random, or wall-clock input and retries once if the
projection or audit revision changes during assembly.

## Draft payload

`DraftPayload` is a closed version-1 object. MCP `draft_save` exposes the same fields plus the
required mutation field `approved: true`.

| Field | Required | Contract |
| --- | --- | --- |
| `schema_version` | yes | Literal `1`. |
| `draft_id` | no | Existing or caller-allocated `DRAFT-YYYYMMDD-<slug>-NN`; omitted for local allocation. |
| `title` | yes | Printable single-line title. |
| `recipient_uri` | yes | Canonical local `workctx://.../person/PER-...` URI. |
| `purpose` | yes | Printable single-line communication purpose. |
| `format` | yes | `chat`, `email`, `status_update`, or `documentation`. These are content shapes, not transports. |
| `body` | yes | Agent-authored Markdown, up to 100,000 characters. |
| `task_uri` | no | Canonical local parent-task or subtask URI. |
| `source_refs` | no | Up to 100 unique durable references supporting the draft. |
| `author_id` | yes | Agent actor identity recorded in the transaction event. |
| `agent` | yes | Agent implementation recorded in the transaction event. |
| `model` | yes | Model identity recorded in the transaction event. |

The body is not summarized, rewritten, or selectively regenerated. Canonical Markdown storage
normalizes line endings and the structural trailing newline under ADR 0005; within that
representation, uncertainty headings, wording, ordering, and unresolved questions are retained
verbatim. Callers should put all material uncertainty in the body rather than relying on a
generated summary.

Every string in the payload is checked with `contains_possible_secret` before projection reads
or transaction construction. A possible secret refuses the complete save with a content-free
error; it is never copied into the outbox or ledger.

## Identity and lifecycle

No existing domain ID family represents a draft, so this package uses the grammar
`DRAFT-YYYYMMDD-<slug>-NN`. The date is the UTC allocation day, the slug is lowercase ASCII
letters and digits separated by single hyphens, and `NN` is the first free `01` through `99`
for that day and slug. This family is already accepted by canonical filename validation. Draft
URIs use `workctx://<context-id>/draft/<draft-id>`.

An omitted ID creates a newly allocated draft. A supplied ID creates that identity when absent
or revises it when present. Revisions preserve `id`, URI, and `created_at`, replace the reviewed
payload fields, advance `updated_at`, and append another ledger event. Listing is lexical by ID.

Both create and revise operations use an entity document payload in a typed transaction
proposal, the verified ledger head as `base_revision`, and the context's local-mutation policy.
`approved` is the runtime signal for that local mutation; it does not change draft status and
does not authorize delivery. The returned `ApplyResult` authenticates local persistence only.

## No-send boundary

The drafting package has no send, publish, post, forward, browser, mail, network, connector, or
plugin-execution primitive. It cannot turn `delivery_state: unsent` into a delivery claim.
There is no Phase 1 delivery receipt because there is no Phase 1 delivery interface.

Any future connector must be a separate external-write operation. It must identify the exact
system, recipient, content, and action; obtain explicit approval immediately before delivery;
and return an authenticated external receipt. Approval to gather context or persist a draft
must never be reused as that delivery approval.
