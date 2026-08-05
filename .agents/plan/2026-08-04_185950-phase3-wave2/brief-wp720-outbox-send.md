# Brief: WP-720 — Outbox send engine and the github channel (D-053)

Codex worker, worktree `.worktrees/WP-720`, branch `agent/WP-720-outbox-send`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read design-external-writes.md
(.agents/plan/2026-08-03_232026-phase3-wave1/), D-053, and ADR 0013 FIRST.
Study src/workctx/drafting/ (DraftPayload, delivery_state, get_draft),
src/workctx/connectors/ (secret containment patterns to REUSE), and the
suggestion CLI for the --yes envelope pattern.

## The guarantee this changes

docs/security-and-privacy.md currently states the alpha has NO external-write
capability. Your change narrows that guarantee and MUST update that document
in the same change, honestly: external writes exist only through
`workctx outbox send`, one draft, one recipient, one explicit approval per
operation, ledger-audited.

## Scope

1. Send engine in `src/workctx/drafting/` (new module file(s)):
   - channel adapter seam (`SendChannel` protocol): v1 ships ONE adapter,
     `github` — posts a draft body as a comment on an issue or PR;
   - target grammar for github: `owner/repo#number` (validated, no URLs);
   - token chain for github (reuse ADR 0013 discipline, values leak-proof):
     secret_ref `github-token` -> env `GITHUB_TOKEN` -> `gh auth token`
     subprocess fallback (stdout captured, never logged); token reaches only
     the outgoing httpx request (mirror the connector transport-boundary
     pattern);
   - `preview_send(root, draft_id, channel, target)` -> typed preview:
     channel, target, resolved recipient display, full body, draft content
     hash, and a SEND FINGERPRINT = sha256 over (draft hash + channel +
     target);
   - `send(root, draft_id, channel, target, *, approved, fingerprint,
     transport=None, clock=None)`: refuses without approved=True; refuses if
     the current draft+target fingerprint differs from the passed one (the
     operator approves EXACTLY what was previewed); re-runs
     contains_possible_secret over the outgoing body as a last gate; on
     delivery success, ONE approved transaction updates the draft
     (delivery_state -> sent, delivery provenance: channel, target, remote
     comment URL/id, sent_at) and commits a ledger event; on delivery
     failure, NO canonical change — typed, content-free diagnostic;
   - hard rails: no batch API, exactly one draft per call, drafts already
     sent refuse a resend (delivery_state guard).
2. CLI group `outbox` in cli.py: `outbox send <DRAFT-ID> --via github
   --target owner/repo#N [--yes] [--json]`. Without --yes: preview envelope
   including the fingerprint. With --yes: requires `--fingerprint <value>`
   in JSON mode (scripts must echo what they approved); in human mode the
   preview+confirm happens in one flow by rerunning preview internally and
   comparing. Envelope rows in cli-envelope.md.
3. docs/reference/outbox-send.md (behavior, rails, github target grammar) +
   the security-and-privacy.md update + one line in the drafting reference.
4. NO MCP changes (draft_send comes later with ADR 0014 ratification). NO
   other channels. NO scheduling.

## Do NOT touch

Anything outside: `src/workctx/drafting/**`, `src/workctx/cli.py` (outbox
group), `tests/drafting/**`, `tests/cli/test_outbox_cli.py` (new),
`docs/reference/outbox-send.md`, `docs/reference/drafting.md` (one line),
`docs/reference/cli-envelope.md` (rows), `docs/security-and-privacy.md`
(external-writes section only). No new dependencies.

## Tests required

Mocked transport ONLY (autouse guard against real HTTP, like connectors);
fictional repos. Preview determinism and fingerprint math; approval refusal;
fingerprint mismatch refusal (draft edited after preview; target swapped);
resend refusal; secret-scan last-gate refusal (fictional token in body);
success path: comment posted (mock), draft updated + ledger event in one
apply, provenance recorded; failure path: no canonical change; token
containment (absent from results, errors, logs, workspace — reuse the
connector proof style); CLI envelopes for preview/send/failure. Full gate;
declare sandbox limits explicitly. Draft ADR 0014 content in your REPORT
(not in docs/adr/).
