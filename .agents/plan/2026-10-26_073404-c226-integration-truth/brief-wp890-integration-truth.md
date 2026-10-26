# Brief: WP-890 — Integration location truth, resolve-don't-guess, human search results (C-226)

Codex worker, worktree `.worktrees/WP-890`, branch `agent/WP-890-integration-truth`.
No commits; final message = report. `.agents/` read-only (mirrors and
`_HISTORICAL_TEMPLATE_BRIDGE_HASHES` are lead-reconciled). Read C-226 in
`.agents/status/phase2-candidates.md` first. Verify the contract in code
before writing: template `02_knowledge/integrations/` and
`90_integrations/README.md`, `_paths.py` zones, the canonical store's
entity-type placement, and `migration/inventory.py`.

## Contract

1. Truthful location everywhere. Integration entities (`INT-*`, entity
   type integration) live in `02_knowledge/integrations/`;
   `90_integrations/` holds connector configuration and the source
   catalog only. Fix EVERY prompt surface and doc that says otherwise:
   the three bridges, canonical context template AGENTS.md (+ mirror via
   sync script), `workctx guide` (ownership/routing rows and JSON),
   packaged skills (grep all of `src/workctx/resources/agent_kit/skills`
   for `90_integrations`, not only bootstrap-session), docs/guides and
   docs/reference. Edit the existing C-218/C-219 sentences in place —
   do not add competing rules. `workctx connector list` / connector docs
   keep pointing at `90_integrations/` correctly.
2. Resolve, never guess. One rule extending the same bridge sentence and
   the template: canonical items are found by `workctx search <q> --json`
   and resolved with `workctx ref show <workctx-uri> --json` (which
   returns the real `source_path`); a supposed path not existing never
   proves an item is missing.
3. Human search output lists the hits: rank, entity type, ID/title, the
   context-relative source path, and the canonical URI for the top N
   results (default 10, `--limit` respected), then the count line.
   Compact, width-safe, no snippet bodies required. JSON unchanged.
   Tests pin the human format (the suite pins console width).
4. Validator: new diagnostic `CTX-MISPLACED-ENTITY` (warning) when an
   integration entity file lives outside `02_knowledge/integrations/`
   (generalize to any typed entity whose canonical directory is known, if
   the store exposes that mapping cleanly; otherwise integration only),
   repair action naming the correct directory. Register in
   DIAGNOSTIC_DEFINITIONS + validation-diagnostics.md row. Working dirs
   (WP-850) are exempt.

## Allowed paths

agent kit bridges (3) and packaged skills, canonical context template
AGENTS.md (+ sync script for the mirror), `src/workctx/guide.py`,
`src/workctx/cli.py` (search rendering), `src/workctx/retrieval/**` (only
if rendering needs a helper), `src/workctx/validation/**`,
`docs/guides/**`, `docs/reference/**`, `tests/**`.

## Tests

Content assertions: no prompt surface or guide routing names
`90_integrations/` as an integration-entity home; the truthful sentence
and the resolve-don't-guess sentence appear once per surface. Human
search format pinned. Misplaced-entity diagnostic positive/negative and
working-dir exemption. Full gate where the sandbox allows; limits
declared.
