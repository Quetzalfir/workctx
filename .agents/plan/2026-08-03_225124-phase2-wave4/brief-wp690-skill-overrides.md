# Brief: WP-690 — Per-context skill overrides with three-way upgrade markers

Codex worker, worktree `.worktrees/WP-690`, branch `agent/WP-690-overrides`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read the design doc
(.agents/plan/2026-08-03_200758-phase2-wave3/design-c202-c212.md), D-045, and
the WP-630 personalization loader (your closest pattern) FIRST.

## Scope

1. Override discovery: `06_overrides/skills/<skill-name>/SKILL.md` inside the
   context (D-045: per-context only). User-owned files: never generated,
   never overwritten, never executed; size cap and secret scan exactly like
   personalization layers (refusal names file + line only).
2. Skill rendering/install merge: when an override exists for a packaged
   skill, the INSTALLED skill output is the override content, prefixed with a
   provenance header (override path + the packaged skill version/hash it was
   written against). Unknown skill names in 06_overrides = typed warning in
   `agent status`, never an error.
3. Three-way upgrade marker: the provenance header records the packaged
   skill content hash at adoption time. On install/upgrade, if the packaged
   skill changed since (hash mismatch), `agent install` plan and
   `agent status` surface "override written against an older packaged skill"
   with the three hashes (packaged-at-adoption, packaged-now, override) —
   surfacing ONLY; never auto-merge, never block.
4. Removing the override file restores packaged behavior on next install
   (prove by test).
5. `docs/guides/personalization.md` gains an overrides section (how to adopt,
   the upgrade marker, how to remove).

## Do NOT touch

Anything outside: `src/workctx/adapters/agents/**`, `tests/agents_setup/**`,
`docs/guides/personalization.md`. The skill-lint rules apply to override
CONTENT the same as packaged skills at install time — reuse the existing
lint, do not fork it. `06_overrides/` template changes are OUT of scope (the
directory is created on demand); if the canonical-store zone validation
rejects the path, STOP and report the blocker with the exact rule.

## Tests required

Discovery none/one/many; merge output with provenance; stale-marker
surfacing across a simulated kit upgrade (three hashes correct); removal
restores packaged; size/secret refusals; unknown-name warning; install-plan
preview lists overrides before approval; cross-platform paths. Fictional
content; full gate; declare sandbox limits.
