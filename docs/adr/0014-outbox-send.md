# ADR 0014 — Approval-gated outbox send

- Status: accepted
- Date: 2026-08-04
- Deciders: implementation lead, ratifying the WP-720 design under the
  operator's D-053 approval; supersedes the absolute no-external-writes
  posture recorded in the 0.1.0-alpha security documentation

## Context

Until this decision the product guaranteed that no code path could write to
any external system: drafts under `05_outbox/` were terminal and delivery was
a manual human act. That guarantee was valuable but left the core loop —
evidence to knowledge to communication — open at its last step. The operator
approved closing it with the narrowest workable capability.

## Decision

1. External writes exist only through the outbox send engine, surfaced as
   `workctx outbox send`. Draft saving stays local; saving approval never
   authorizes delivery.
2. V1 registers one `SendChannel` adapter: `github`, posting the exact draft
   body as one issue or pull-request comment. Targets use `owner/repo#number`;
   URLs and recipient lists are refused.
3. Preview returns the full outgoing body, the resolved recipient display,
   the canonical draft SHA-256, and a send fingerprint:
   `sha256(draft_hash + channel + target)`.
4. Send requires `approved is True` and an exact current fingerprint. JSON
   callers echo `--fingerprint`; human callers get a fresh preview and
   interactive confirmation in one invocation.
5. Send re-runs the possible-secret predicate over the outgoing body
   immediately before authentication and transport; a hit refuses delivery.
6. GitHub authentication resolves: ADR 0013 secret reference `github-token`,
   then `GITHUB_TOKEN`, then a captured `gh auth token`. Values stay opaque
   and are revealed only into the cloned request at the HTTPX transport
   boundary.
7. No redirects are followed and no automatic retry exists anywhere.
8. A verified HTTP 201 with a positive comment ID and canonical comment URL
   is the only success condition.
9. After remote success, one approval-required local transaction marks the
   draft `sent` with channel, target, comment ID, URL, and UTC timestamp,
   and appends one hash-chained ledger event, preconditioned on the
   previewed draft hash and sharing one injected clock.
10. Sent drafts can never be re-unsent, re-previewed for delivery, or resent.
11. Remote failure produces a typed, content-free diagnostic and zero
    canonical or ledger mutation.
12. Remote success followed by local recording failure raises a
    reconciliation-required error carrying only the safe remote ID/URL and
    instructing the operator not to resend.
13. A crash between remote success and local commit can leave no local
    receipt. Cross-system atomicity is impossible with this API and is not
    claimed; the window is documented instead.
14. The ledger actor is the reserved system actor `workctx-outbox-send`;
    the approval gate proves runtime authorization, not OS-user identity.
15. MCP send, additional channels, attachments, batch send, scheduling,
    compensation, and background delivery are all outside this ADR and each
    requires its own decision.

## Consequences

- The public security guarantee narrows from "no external writes" to "one
  draft, one recipient, one explicit fingerprint-pinned approval through
  outbox send"; the security documentation changes in the same commit that
  ships the capability.
- Successful deliveries become durable, queryable canonical provenance
  backed by the audit ledger.
- Delivery failure cannot half-update local state; remote-success/local-
  failure is an explicit reconciliation path, never a silent state.
- Future channels must implement the same protocol and rails independently;
  reusing the read-connector runtime was rejected because reads and writes
  need different approval and provenance models.

## Rejected alternatives

Copy/paste-only forever (rejected by the operator, D-053); approval without
fingerprint pinning (content or target could drift after review); batch or
scheduled sends and automatic retries (multiply irreversible blast radius);
marking drafts sent before the remote confirms (false delivery claims);
compensating deletes of remote comments (another fallible external write);
a two-transaction pending/sent lifecycle (v1 requires no canonical change on
remote failure).
