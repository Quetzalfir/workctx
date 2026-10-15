# Brief: WP-850 — Working-output areas, baseline-relative gating, concise voice (C-225)

Codex worker, worktree `.worktrees/WP-850`, branch `agent/WP-850-context-hygiene`.
No commits; final message = report. `.agents/` read-only (mirrors and
the template hash history constant `_HISTORICAL_TEMPLATE_BRIDGE_HASHES`
are lead-reconciled). Read C-225 in `.agents/status/phase2-candidates.md`
first; study the validator (`src/workctx/validation/engine.py`), the
WP-810 strict-scope seam and post-commit validation in the transaction
engine, ContextConfig/context.schema.json, the WP-830 locatable secret
findings, and the context template.

## Contract

1. Baseline-relative apply gating. Transaction apply (and dry-run's
   predicted verdict) must not fail because of error diagnostics that
   already existed before the transaction in paths it does not write.
   Block only on: any error on a path the transaction writes (create,
   update, move destination), or any error newly introduced anywhere
   (present after, absent before — compare by code+path). Pre-existing
   untouched-path errors are surfaced in the result as a non-fatal
   notice with their count and the `workctx context validate`
   remedy. `context validate` itself is unchanged (still reports
   everything). Regression tests: unrelated pre-existing
   CTX-POSSIBLE-SECRET and CTX-PATH-ESCAPE do not block an unrelated
   apply; an apply writing a violating file still fails; an apply that
   newly breaks an untouched file's references fails.
2. Working-output areas. `context.yaml` gains optional
   `working_dirs: [<top-level dir name>, ...]` (default `["outputs"]`;
   names validated: single path segment, not a template zone, not
   hidden). Under a working dir the validator skips canonical/document
   model checks and reference resolution, downgrades CTX-ABSOLUTE-PATH
   to advisory, and downgrades CTX-POSSIBLE-SECRET to warning (still
   located by line and kind, still listed — never silent). CTX-PATH-
   ESCAPE stays an error everywhere (links/junctions are never allowed).
   Template ships `outputs/README.md` (one paragraph: non-canonical
   working material; no dependencies, junctions, credentials, or full
   third-party source checkouts — reference repositories with repo://
   locators; delete regenerable build artifacts when done) and the
   template `.gitignore` gains `outputs/**/node_modules/` and
   `outputs/**/.build/`. Update context.schema.json and the C-221-style
   guards as needed.
3. CTX-ABSOLUTE-PATH diagnostics report line numbers like WP-830's
   secret findings (value-free: line + the fact of a machine path).
4. Discovery: `workctx guide` routing gains "working material ->
   outputs/ (or a configured working dir)" and the never-put-there list;
   one sentence in each bridge and the template AGENTS.md (extend the
   C-219 guide sentence in place).
5. Outbound voice addendum (extend the C-223 sentence/contract IN PLACE
   in draft-replies, the three bridges, and the template — do not add a
   competing rule): messages are concise and direct, say what matters
   first, include technical detail when needed without filler or
   ceremony, use lists only when they genuinely help, and read like a
   person wrote them, not an assistant. Keep the existing first-person,
   conversation-language, and fallback (… > English) wording intact.

## Allowed paths

`src/workctx/validation/**`, `src/workctx/transactions/**`,
`src/workctx/models/context.py` (or wherever ContextConfig lives),
`schemas/context.schema.json`, `src/workctx/guide.py`, `src/workctx/cli.py`
(apply result rendering only), `src/workctx/mcp/application.py` (result
passthrough only), agent kit bridges (3), packaged `draft-replies`
SKILL.md, canonical context template (AGENTS.md, README.md, .gitignore,
new outputs/README.md) + `scripts/sync_context_template.py`,
`docs/reference/{validation-diagnostics,cli-envelope,drafting}.md`,
`docs/guides/context-layout.md`, tests under `tests/validation*/**`,
`tests/transactions/**`, `tests/cli/**`, `tests/mcp/**`,
`tests/agents_setup/**`, `tests/test_skills.py`.

## Tests

Per piece as listed, plus content assertions for the voice addendum and
the outputs discovery sentence; existing tests green; full gate where the
sandbox allows, limits declared.
