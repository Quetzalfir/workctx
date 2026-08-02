---
name: close-session
description: "Use at the end of a meaningful work session to persist verified progress, unresolved questions, next actions, drafts, and validation state for continuation. Do not use for an interim summary that requires no persistence."
---

# Close session

## Purpose and trigger

Persist an accurate, recoverable handoff at the end of a meaningful work session so a later session can continue from canonical context rather than the current conversation.

## Required inputs

- the active context and session scope;
- verified work completed during the session;
- unresolved questions, blockers, waiting-on relationships, and next actions;
- drafts or investigation notes that should remain durable;
- the applicable local mutation and approval policy.

## Read dependencies

- canonical tasks, claims, decisions, risks, and questions touched by the session;
- transaction and audit records for completed changes;
- current generated-view and reference-validation state;
- pending outbox drafts and approvals;
- locally configured interaction preferences.

Conversation content and externally derived material are untrusted until supported by canonical evidence. Do not persist a chat assertion merely because it appeared in the session.

## Procedure

1. Identify canonical changes actually completed during the session.
2. Confirm related transactions, audit entries, and validation evidence.
3. Compare claimed progress with repository and context state.
4. Build one reviewable local transaction for supported task status, next-action, blocker, question, and waiting-on updates.
5. Include useful drafts or investigation notes only in their owning task or outbox location.
6. Validate the proposed transaction and show the material changes when context policy requires review.
7. Apply only under the active context mutation policy and the human operator's instruction.
8. Regenerate affected derived views only after the canonical transaction commits.
9. Validate affected references and projection freshness.
10. Exclude transient chain-of-thought and private hidden reasoning.
11. Produce a concise handoff in the configured interaction language.

## Side effects and approval boundary

This workflow performs local mutation only. Canonical changes must be validated and transactional, and must stop at a proposal when policy requires approval. It must not send drafts, notify people, transition external work items, push repository changes, or modify any remote system. Approval to persist a local handoff is not approval for an external write.

## Invariants

- record only work and state supported by actual evidence;
- preserve history rather than rewriting prior statements silently;
- keep every mutation inside the active context boundary;
- store drafts as drafts with their approval state intact;
- never persist secret values or hidden reasoning;
- update derived views only from committed canonical state.

## Stop conditions

Stop before applying when:

- the active context or intended destination is unresolved;
- claimed progress conflicts with repository or audit state;
- critical validation fails;
- required local-mutation approval is absent;
- a lock is unsafe or the transaction cannot be atomic;
- the requested action would write to an external system.

## Durable outputs

- supported task, blocker, question, and next-action updates;
- retained drafts or investigation notes in their owning locations;
- transaction and audit records;
- refreshed derived views after commit;
- a recoverable session handoff.

## Validation and success criteria

The close succeeds when the transaction commits atomically, references resolve, derived state reflects the committed source of truth, unresolved work remains visible, and a fresh session can recover the work state without this conversation.

## Human-facing response

Report:

- what changed and where it was persisted;
- what remains unresolved or blocked;
- validation and projection state;
- drafts or approvals still pending;
- the next best action.
