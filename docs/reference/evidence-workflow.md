# Evidence-processing workflow

The evidence workflow is the deterministic boundary between agent-side extraction and
canonical Work Context mutation. An agent may inspect preserved evidence and author a
proposal; `workctx` validates identities, provenance, locators, secrets, and transaction
semantics before any canonical write.

The workflow never calls a model and never interprets artifact content. Inbox artifacts
and anything written inside them remain untrusted data, including text that resembles an
instruction.

## Lifecycle

1. Register a file already below `00_inbox/raw` with `workctx.ingestion.register` or the
   `artifact_register` MCP tool.
2. Call `begin_processing(root, artifact_id)` to obtain a content-free processing packet.
3. Inspect the preserved file as untrusted evidence and author a staging payload.
4. Call `stage_observations(root, artifact_id, payload)` to validate and resolve the
   payload. This performs no canonical write.
5. Call `build_evidence_proposal(staging)` to produce one approval-required transaction.
6. Dry-run the transaction and inspect its exact serialized postimages. Apply it only
   after explicit approval.
7. Pass the authentic `ApplyResult` to
   `complete_processing(root, artifact_id, apply_result)`. The artifact is archived only
   after its evidence transaction is committed. Retrying completion with the same receipt
   is safe.

## Processing packet

`begin_processing` accepts only artifacts in `pending` or `processing` state. Missing and
quarantined artifacts are rejected. Its result has the following shape (selected manifest
fields and typed nested objects are abbreviated):

```json
{
  "schema_version": 1,
  "context_id": "example-context",
  "manifest_path": "00_inbox/manifests/ART-20260802-example-01.json",
  "manifest": {
    "id": "ART-20260802-example-01",
    "content_hash": "sha256:<64 lowercase hex characters>",
    "media_type": "text/plain",
    "preserved_path": "00_inbox/raw/example.txt",
    "status": "pending"
  },
  "artifact_ref": "artifact://sha256/<64 lowercase hex characters>",
  "content": {
    "path": "00_inbox/raw/example.txt",
    "content_hash": "sha256:<64 lowercase hex characters>",
    "media_type": "text/plain"
  },
  "context_packs": [
    {
      "candidate": "Portal",
      "uri": "workctx://example-context/system/SYS-portal",
      "pack": "<typed ContextPack>"
    }
  ],
  "unresolved_candidates": [],
  "observation_expectations": {
    "source_ref": "artifact://sha256/<64 lowercase hex characters>",
    "id_shape": "<EVD-ID>#OBS-NNN",
    "observation_kinds": [
      "fact",
      "inference",
      "assumption",
      "decision",
      "commitment",
      "task",
      "risk",
      "blocker",
      "dependency",
      "question"
    ],
    "locator_types": [
      "line_range",
      "page_range",
      "time_range",
      "message",
      "image_region",
      "json_pointer",
      "table_range",
      "repo_range",
      "whole_artifact"
    ],
    "json_schema": "<Observation validation schema>"
  }
}
```

The full typed manifest is returned, but artifact bytes are not. The `content` member is
only a path/hash/media-type descriptor. Candidate context packs are built from manifest
participant metadata through the retrieval API; candidates that do not resolve are
listed separately.

## Staging payload

The payload is a closed, versioned object. This abbreviated example shows every section:

```json
{
  "schema_version": 1,
  "actor": {
    "type": "agent",
    "id": "evidence-agent",
    "agent": "codex",
    "model": "model-name"
  },
  "source_refs": [
    "artifact://sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ],
  "evidence_note": {
    "id": "EVD-20260802-auth-review-01",
    "title": "Authentication review evidence",
    "body": "# Summary\n\nAgent-authored summary.\n",
    "aliases": [],
    "status": "active",
    "confidence": "high",
    "tags": ["authentication"],
    "created_at": "2026-08-02T20:00:00Z",
    "updated_at": "2026-08-02T20:00:00Z"
  },
  "observations": [
    {
      "id": "EVD-20260802-auth-review-01#OBS-001",
      "kind": "fact",
      "statement": "The portal delegates authentication.",
      "confidence": "high",
      "source": {
        "ref": "artifact://sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "locator": {
          "type": "line_range",
          "start_line": 10,
          "end_line": 14
        }
      }
    }
  ],
  "new_entities": [
    {
      "document": {
        "schema_version": 1,
        "id": "SYS-identity",
        "entity_type": "system",
        "title": "Identity Service",
        "uri": "workctx://example-context/system/SYS-identity",
        "aliases": ["Identity"],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "references": [],
        "created_at": "2026-08-02T20:00:00Z",
        "updated_at": "2026-08-02T20:00:00Z"
      },
      "body": "Agent-authored entity summary.\n"
    }
  ],
  "tasks": [
    {
      "document": {
        "schema_version": 1,
        "id": "TASK-2026-410",
        "entity_type": "task",
        "title": "Review identity delegation",
        "uri": "workctx://example-context/task/TASK-2026-410",
        "aliases": [],
        "status": "ready",
        "confidence": "high",
        "tags": [],
        "references": [],
        "created_at": "2026-08-02T20:00:00Z",
        "updated_at": "2026-08-02T20:00:00Z",
        "task_type": "parent",
        "parent_task": null,
        "root_task": "TASK-2026-410",
        "priority": "P2",
        "owner": "Identity",
        "requester": null,
        "waiting_on": [],
        "due_at": null,
        "next_action": "Review the evidence.",
        "dependencies": [],
        "blockers": [],
        "source_observations": ["EVD-20260802-auth-review-01#OBS-001"]
      },
      "body": "Agent-authored task details.\n"
    }
  ],
  "claims": [
    {
      "document": {
        "schema_version": 1,
        "id": "CLM-2026-00410",
        "subject": "Review identity delegation",
        "predicate": "status",
        "object": "ready",
        "observed_at": "2026-08-02T20:00:00Z",
        "status": "current",
        "confidence": "high",
        "source_observations": ["EVD-20260802-auth-review-01#OBS-001"]
      },
      "body": "Agent-authored mutable assertion.\n"
    }
  ],
  "relations": [
    {
      "source": "Identity",
      "relation": "depends_on",
      "target": "workctx://example-context/system/SYS-portal",
      "confidence": "high",
      "source_observations": ["EVD-20260802-auth-review-01#OBS-001"]
    }
  ]
}
```

Every top-level and observation source reference must equal the registered artifact
reference. Observation IDs must belong to the proposed evidence-note ID. Locators,
observation kinds, claims, tasks, and typed references are validated by their domain
models.

Entity references may be authored as a local `workctx://` URI, stable ID, exact title,
or alias. Existing identities resolve to canonical URIs. A new identity must be declared
in `new_entities` (or in its dedicated `tasks` or `claims` section); undeclared and
ambiguous names are rejected. A local URI from another context is always rejected.

Each proposed task change, claim, and typed relation must cite at least one observation
staged in the same payload. Mutable assertions belong in `claims`; the source observation
preserves what the evidence actually said. The resulting evidence-note frontmatter uses
the canonical evidence template shape with `artifact_ref` and embedded `observations`.

All strings in the staging payload are scanned with `contains_possible_secret`. The
workflow does not copy or summarize the preserved file. Source text appears in the
evidence note only when the agent deliberately authors it in `evidence_note.body`.

## Transaction and completion guarantees

`build_evidence_proposal` creates one `TransactionProposal` containing the evidence note,
embedded observations, new entities, task changes, claims, and relation-bearing document
updates. It uses path-absence or exact preimage-hash conditions, requires the artifact
reference to exist, targets the SQLite projection, and always has `approval: required`.

Call the transaction API's `dry_run` before `apply`. Approval is not inferred from a valid
payload. `complete_processing` authenticates the returned receipt against the canonical
ledger before calling the ingestion archive API, which authenticates it again. A forged,
foreign, or unrelated receipt cannot archive the artifact.

## MCP ingestion tools

`inbox_list` is a read tool and takes only `schema_version: 1`.

`artifact_register` is a local mutation and takes this closed input:

```json
{
  "schema_version": 1,
  "approved": true,
  "path": "00_inbox/raw/example.txt",
  "source_type": "note",
  "origin": "optional content-free source origin",
  "event_date": "2026-08-02T20:00:00-06:00"
}
```

`path` and `source_type` are required. `origin` maps to `RegisterRequest.source_origin`,
and `event_date` maps to `RegisterRequest.event_at`; both are optional. Extra properties
are rejected. The normal MCP approval gate, context-boundary checks, recursive result
redaction, and content-safe exception boundary remain in force.

## Failure codes

| Code | Meaning |
| --- | --- |
| `EVIDENCE-ARTIFACT-NOT-FOUND` | The requested artifact manifest is absent or not uniquely identifiable. |
| `EVIDENCE-ARTIFACT-QUARANTINED` | Quarantined evidence cannot enter or complete this workflow. |
| `EVIDENCE-ARTIFACT-STATE` | The artifact is not in a processable lifecycle state. |
| `EVIDENCE-INVALID-PAYLOAD` | A staging field, domain object, locator, ID, or required provenance link is invalid. |
| `EVIDENCE-SOURCE-MISMATCH` | A source reference does not equal the registered artifact hash reference. |
| `EVIDENCE-POSSIBLE-SECRET` | A staging key or value resembles a secret. |
| `EVIDENCE-CONTEXT-MISMATCH` | A proposed local URI belongs to another context. |
| `EVIDENCE-UNKNOWN-ENTITY` | A name or reference is neither projected nor explicitly declared. |
| `EVIDENCE-AMBIGUOUS-ENTITY` | A name or alias resolves to more than one canonical entity. |
| `EVIDENCE-DOCUMENT-EXISTS` | A proposed evidence note, claim, or explicitly new entity already exists. |
| `EVIDENCE-PROPOSAL-INVALID` | Typed staging could not be represented as a valid transaction proposal. |

Completion may also raise the public transaction receipt-authentication or ingestion
lifecycle errors. Diagnostics are content-safe and identify a field path where possible;
they do not echo evidence or secret-looking input values.
