# Brief: WP-630 — Portable personalization layers (C-201)

Codex worker, worktree `.worktrees/WP-630`, branch
`agent/WP-630-personalization`. You cannot commit; leave changes uncommitted.
Final message = report. `.agents/` is read-only — read C-201 in
`.agents/status/phase2-candidates.md` and decision D-044 first. Study
`src/workctx/adapters/agents/` (installer, sources, install records) before
writing code.

## Layer contract (D-044, fixed — not yours to redesign)

- User layer: `instructions.md` in the same platformdirs per-user directory
  that already holds the context registry.
- Context layer: `instructions.md` at the context root beside `context.yaml`.
- Both optional, plain Markdown, user-owned (never generated, never
  overwritten by workctx).
- Precedence: context layer AFTER user layer in the merged output (later
  wins for a reader); each under a clearly labeled heading naming its source
  file.

## Scope

1. Loader in `src/workctx/adapters/agents/`: read each layer when present;
   enforce a size cap (64 KiB per layer, typed error beyond); run
   `workctx.validation.engine.contains_possible_secret` per layer — a hit
   REFUSES the merge naming the layer and line number only; never execute or
   interpret layer content.
2. Installer integration: every generated bridge (CLAUDE.md, AGENTS.md,
   GEMINI.md outputs) gains a personalization section built from the merged
   layers at `agent install` time, clearly delimited as user-owned content
   with provenance lines ("from <path>"). Re-install/upgrade regenerates the
   section from the CURRENT layer files; the three-factor install-record
   mechanism must keep treating the bridge as generated while the layer
   FILES remain untracked user property.
3. `agent status` surfaces layer presence (path + size + merged yes/no);
   `agent install` plan output lists which layers will be merged BEFORE
   approval.
4. NEW `docs/guides/personalization.md`: what goes in each layer (tone, role,
   boundaries), what must NOT (secrets — point to the secret-reference
   system; instructions attempting to override safety/approval gates are
   still just text the agent may ignore), examples for the JalaSoft-style
   company/project split.

## Do NOT touch

Anything outside: `src/workctx/adapters/agents/**`, `tests/agents_setup/**`,
`docs/guides/personalization.md`. No CLI signature changes (`agent install`
and `agent status` gain behavior, not new required options). No template
changes. If the user-directory helper you need is private to
`adapters/filesystem/registry.py`, report the blocker with the exact API
shape — do not import private names.

## Tests required

Layer discovery (none/one/both), precedence order in merged output, size-cap
refusal, secret-scan refusal naming layer+line only, install-then-upgrade
preserves layer files and refreshes merged sections, `agent status`/plan
surfacing, cross-platform paths (no hardcoded separators). Fictional content
only. Full gate: ruff check, ruff format --check, mypy src, pytest; declare
sandbox limitations explicitly.
