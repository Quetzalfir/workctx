# Brief: WP-790 — File placement and ownership guide (C-219)

Codex worker, worktree `.worktrees/WP-790`, branch `agent/WP-790-ownership-guide`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-219 in `.agents/status/phase2-candidates.md`
first. Study the context template zones, the adapter manifest ownership
model (`src/workctx/adapters/agents/`), the personalization layers, and
docs/guides/context-layout.md so the guide states the REAL contract, not
an invented one.

## Contract

1. New command `workctx guide [--json] [--context PATH]` resolving a
   context like other read commands (register-on-use hook applies). It
   prints, for the resolved context:
   - Ownership table: every top-level path the template or adapters
     create (zones 00-99, context.yaml, instructions.md, 06_overrides/,
     .agents/skills/, AGENTS.md/CLAUDE.md/GEMINI.md, .mcp.json, .codex/,
     04_views/, 98_state/) with ownership class — canonical-via-proposals,
     operator-owned, adapter-managed, generated, machine-state — and the
     edit policy for each (edit freely / through proposals or transactions
     / preserved-but-freezes-updates / never edit by hand + the command
     that regenerates it).
   - Routing table ("where does it go"): person fact -> person entity;
     access or process fact -> integration entity under 90_integrations/
     or system entity; standing operator preference -> instructions.md
     (context) or user-level instructions.md (all contexts); evidence ->
     workctx inbox add; task/work item -> 03_work via proposal; outbound
     draft -> 05_outbox via draft flow; workflow customization ->
     06_overrides/skills/<name>/SKILL.md; never secret values anywhere,
     names only.
   - Never-edit list with the escape hatch sentence: if a generated file
     seems to require a manual edit, stop, run `workctx agent repair` or
     `workctx agent refresh`, or ask the operator — editing it directly
     freezes it out of updates and blocks refresh.
   Content is deterministic packaged data (no filesystem scanning beyond
   context resolution), one authoritative structure rendered to human
   table and `--json`. Human output stays compact (one screen-ish).
2. Discovery: ONE sentence added to each of the three bridges (and the
   context template AGENTS.md bullet list, plus the template README.md
   section) telling agents to run `workctx guide` before creating or
   modifying files whose placement they are not certain of, and that
   generated files are never hand-edited. Also name the command in the
   bootstrap-session skill startup surfaces. Extend the C-218 sections in
   place — no competing rules, keep each bridge's voice.

## Allowed paths

`src/workctx/cli.py`, `src/workctx/guide.py` (new module for the
authoritative structure + rendering), `src/workctx/presentation/__init__.py`
(exports only if needed), agent kit bridges (3), packaged
`bootstrap-session/SKILL.md`, `src/workctx/resources/context_template/
AGENTS.md` and `README.md` via canonical template + `scripts/
sync_context_template.py`, `docs/reference/cli-envelope.md` (rows),
`docs/guides/context-layout.md` (mention the command),
`tests/cli/test_guide_cli.py` (new), `tests/agents_setup/**` (content
assertions only). NOTE: `.agents/skills/bootstrap-session` mirror is
lead-reconciled after delivery — do not touch `.agents/`.

## Tests required

Guide command: human output contains every ownership class and the escape
hatch; `--json` shape pinned (paths, classes, policies, routing entries);
resolves explicit and discovered contexts; exit bands consistent. Bridge
and template content tests extended for the discovery sentence. All
existing tests stay green (bridge-byte tests updated as needed). Full
gate where the sandbox allows; declare limits explicitly.
