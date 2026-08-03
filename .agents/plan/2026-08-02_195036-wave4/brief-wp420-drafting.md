# Brief: WP-420 — Drafting and outbox workflow

Worktree `.worktrees/WP-420`, branch `agent/WP-420-drafting-outbox`. No commits
(sandbox); final message = report. Full gate required; lead reruns outside.

## Read first
AGENTS.md · doc-06 §WP-420 · docs/reference/{context-packs,transactions,views}.md ·
.agents/skills/draft-replies/SKILL.md · decision D-041 in the register.

## Scope
1. `src/workctx/drafting/`: draft lifecycle as canonical `05_outbox/` documents
   (entity_type `draft`, DRAFT-compatible id family — reuse domain ids if one fits,
   else document the grammar in your reference doc; frontmatter per the entity
   contract) written ONLY via `workctx.transactions` proposals. APIs:
   `gather_reply_context(root, person_uri, *, task_uri=None)` → context pack + person
   claims + waiting-on state + recent ledger activity (retrieval + projection + tasks
   APIs; deterministic, no LLM); `save_draft(root, payload, *, approved)` → validated
   draft document proposal (payload carries agent-written body; uncertainty sections
   preserved verbatim; secret refusal via contains_possible_secret) → apply;
   `list_drafts` / `get_draft`. No sending, no publishing, no plugin execution —
   receipts/interfaces only per doc-06.
2. MCP `draft_save` goes live under D-041: complete its placeholder input schema
   ONCE (narrow contracts.py grant: fields matching your save_draft payload;
   approved stays required; additionalProperties false; ADR 0008 fixtures) and wire
   application.py's placeholder to the engine. Do not touch other tools.
3. `docs/reference/drafting.md`: payload contract, draft lifecycle, no-send boundary.

## Do NOT touch
cli.py/presentation, other MCP tools, engines' internals, domain/, schemas/** except
NONE (draft docs validate via the existing entity contract — if that proves
impossible, STOP and report), `.agents/**`, other packages' tests.

## Tests
`tests/drafting/` (+`__init__.py`): gather context determinism, save via real
transaction (ledger event verified), no-send guarantee (no network primitives — assert
module imports), secret refusal, uncertainty preservation, draft listing; MCP
draft_save live in NEW `tests/mcp/test_drafting_tools.py` (approval gate, denial,
sanitization parity). Full gate.
