# Design: the first external-write channel (pause 2) and Graphify/CodeGraph reclassification (pause 3)

Status: operator approved the recommendations 2026-08-04; implementation
waits for operator review of THIS design (explicitly requested). Will become
ADR 0014 at the wave that builds it.

## Pause 2 — Outbox send: draft -> approve -> deliver

### What changes conceptually

Today the product guarantees "no external-write capability at all" (stated in
docs/security-and-privacy.md). Drafts in 05_outbox are terminal: the operator
copy-pastes them manually. This design replaces that absolute guarantee with a
narrower one: "external writes exist ONLY through one approval-gated send
command, one draft, one recipient, one explicit approval per operation."

### Mechanism

1. `workctx outbox send <DRAFT-ID> --via <channel>` WITHOUT `--yes` is a
   preview: renders exactly what would leave the machine — channel, recipient
   resolution, subject/body, attachments none (v1 has none) — and the draft
   content hash. Nothing is sent.
2. With `--yes`: the send adapter delivers, then ONE atomic transaction
   updates the draft (`delivery_state: sent`, delivery provenance: channel,
   remote id/URL, timestamp) and commits a ledger event. The content sent is
   hash-checked against the previewed/approved version — if the draft changed
   since preview, the send REFUSES.
3. Send adapters are channel plugins sharing the connector auth model
   (secret_ref / gh auth); one adapter per channel, deterministic code.
4. Hard rails, non-negotiable:
   - no batch send, no send-all, no scheduling of sends;
   - the agent may PREPARE a send (and may pass approved=true over MCP only
     when the operator instructed that specific send, same as every other
     mutation) — the default posture is that the human runs the --yes;
   - a failed send never half-updates: delivery_state stays unsent with a
     recorded failure diagnostic;
   - quarantine/secret validation already applies to drafts at save; send
     re-runs the secret scan as a last gate;
   - one recipient per draft (already the DraftPayload shape).

### Channel order (feasibility-driven, per D-050 reality)

1. **github** first: comment on an issue or PR (`recipient_uri` maps to a
   repo+number target). Auth is ALREADY solved on the machine (`gh auth`);
   blast radius is small and public-ish by nature; perfect first channel to
   prove the rails.
2. **email** second (SMTP or Graph, pending the operator's corporate
   verification).
3. **teams** third (Graph API permitting; else it stays browser-manual).

### What it gives the operator (use cases)

- "Redacta la respuesta a Alex sobre el bloqueo de SSO" -> review draft ->
  `outbox send DRAFT-... --via email --yes` — the loop evidence -> knowledge
  -> draft -> DELIVERED closes without copy-paste, with the send in the audit
  ledger forever (who, what, when, where, from which evidence).
- Weekly status report view -> draft -> posted as a GitHub issue comment on
  the team's tracking issue.
- PR review findings (investigate-system skill) -> draft -> PR comment.

### Considerations and risks (why the rails exist)

- A mis-send is IRREVERSIBLE — hence per-operation approval, preview-first,
  hash-pinned content, one recipient, no batches.
- The security doc's "no external writes" claim must be updated honestly the
  same day the channel ships (ADR 0014 + docs/security-and-privacy.md + the
  release notes of the next tag).
- Channel outages/auth failures are user-correctable diagnostics, never
  retries-with-side-effects.
- MCP: `draft_send` tool joins the mutation set (approved: true), schema per
  ADR 0012 sequencing rules.

## Pause 3 — Graphify/CodeGraph: reclassified to on-demand (D-051)

No adapter is built now. What this means in practice:

- TODAY, zero code needed: a Graphify/CodeGraph export (JSON/graph dump) is
  ordinary evidence — inbox add, quarantine scan, observations with locators.
  Nothing is blocked.
- IF regular pulls become real, a connector MANIFEST covers it (level 1,
  C-214) with no bespoke code.
- ONLY IF deep semantics prove necessary (auto-mapping graph nodes to
  workctx entities/relations) does a named adapter (level 2) get built, as
  its own work package with its own design.
- The product invariant already promises usefulness without these tools;
  this decision aligns the roadmap with that invariant.
