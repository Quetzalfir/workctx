# Brief: WP-410 — Evidence-processing workflow contracts

Worktree `.worktrees/WP-410`, branch `agent/WP-410-evidence-workflow`. You cannot
commit (sandbox); leave changes uncommitted. Your final message is the report.

## Read first

AGENTS.md · doc-06 §WP-410 · `docs/reference/{inbox,transactions,context-packs}.md` ·
`.agents/skills/process-evidence/SKILL.md` (the workflow you make executable) ·
consolidation `decisions-closed.md` (D-035/D-036 are load-bearing).

## Scope

The deterministic backbone for UC-001: an agent inspects evidence and proposes; the
PRODUCT validates and persists. LLM extraction stays agent-side — you build the rails.

1. `src/workctx/evidence/`:
   - `begin_processing(root, artifact_id)` → processing packet: manifest data, safe
     content descriptor (path + hash + media type — never inline content), a context
     pack around detected/candidate entities (retrieval API), and the observation
     schema expectations. Refuses quarantined or missing artifacts.
   - `stage_observations(root, artifact_id, payload)` → validates agent-produced
     observations/evidence-note payloads against the domain models (Observation,
     locators, D-018 vocabulary), verifies every source_ref matches the artifact hash,
     resolves proposed entity references via the projection (existing → URIs;
     unknown → explicit new-entity declarations), rejects secret-looking values
     (`contains_possible_secret`), and returns a typed staging result. Deterministic;
     no LLM calls.
   - `build_evidence_proposal(staging)` → ONE multi-entity transaction proposal
     (evidence note doc, observations embedded per the WP-110 evidence template shape,
     new entities, claims for mutable assertions, typed relations) ready for
     `transactions.dry_run/apply`.
   - `complete_processing(root, artifact_id, apply_result)` → authenticates the
     receipt and archives via the WP-310 API (which re-authenticates; that is fine).
2. MCP wiring (NARROW grant): replace the two placeholders in
   `src/workctx/mcp/application.py` — `inbox_list` and `artifact_register` — with real
   delegation to `workctx.ingestion` (same envelope/sanitization patterns as sibling
   tools). Do not touch other tools or contracts.py (names/schemas are frozen; if the
   placeholder schema lacks a needed field, STOP and report).
3. `docs/reference/evidence-workflow.md`: the agent-facing contract (packet shape,
   staging payload schema-by-example, failure codes).

## Do NOT touch

cli.py/presentation, contracts.py, engines' internals (ingestion/transactions/
retrieval/validation/adapters consumed via public APIs), domain/, schemas/**,
`.agents/**`, canonical skills, pyproject.toml, other packages' tests
(tests/mcp/test_tools.py etc. — your MCP tests go in NEW files under tests/mcp/).

## Tests required

`tests/evidence/` (+`__init__.py`) with the E2E-002 shape end-to-end on a fixture
context: register (ingestion) → begin → stage (valid + each rejection class: bad
locator, foreign context URI, hash mismatch, secret-looking, unknown entity without
declaration) → proposal dry-run byte-inspection → apply → complete/archive →
trace back from task/claim to observation to artifact (retrieval). Prompt-injection
fixture: artifact content containing instruction-like text NEVER alters behavior
(it is data; assert it lands quoted in the evidence note payload only if the agent
put it there — your rails never parse it). New MCP tests in
`tests/mcp/test_ingestion_tools.py`: both tools live, approval gate on
artifact_register, denial and sanitization parity with siblings. Full gate must pass.
