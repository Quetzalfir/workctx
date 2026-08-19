# Brief: WP-820 — First-class context-local custom skills (C-220)

Codex worker, worktree `.worktrees/WP-820`, branch `agent/WP-820-custom-skills`.
No commits; final message = report. `.agents/` read-only. Read C-220 in
`.agents/status/phase2-candidates.md` first — it records the live incident
(an agent-authored skill parked unregistered broke the inventory check;
the lead's workaround marks the registry operator-edited forever). Study
`src/workctx/adapters/agents/sources.py` (registry parsing, inventory and
skill validation), the WP-760 freshness selector in `service.py`
(registry_operator_edited semantics), and the skill frontmatter contract
(name+description only, description 20-600 chars).

## Contract

1. `registry.yaml` gains an optional `custom_skills:` section (same entry
   shape as `skills:`: id, side_effect_class, optional notes). Entries
   there are context-local skills living in `.agents/skills/<id>/` beside
   packaged ones.
2. A registry whose `skills:` section is byte-equivalent to the packaged
   generation but which adds `custom_skills:` does NOT count as
   operator-edited: packaged-registry refreshes still apply (the refresh
   rewrites `skills:` and PRESERVES the `custom_skills:` section
   verbatim), no merge-pending noise, packaged updates never freeze.
3. Custom skills validate with the SAME frontmatter/link/resource rules
   as packaged ones at plan time, but a custom skill's validation failure
   must name the skill AND the exact rule violated (e.g. description
   length bounds) in the diagnostic — agents author these and need
   actionable errors.
4. Custom skills render to `.claude/skills/` (and `.gemini/skills/`) like
   packaged ones, are preserved across refreshes, never receive packaged
   REPLACE, and are surfaced distinctly: `agent status` lists them as
   custom; `workctx guide` ownership table names `custom_skills` in
   `registry.yaml` + `.agents/skills/<id>/` as the sanctioned home for
   context-local skills (routing entry "custom agent workflow").
5. Migration for the live workaround: a registry whose `skills:` section
   contains an entry NOT in the packaged generation is detected and the
   plan surfaces a repair_action telling the operator to move the entry
   to `custom_skills:` (do not auto-move).
6. Update `schemas/` for the registry shape if a schema exists for it;
   docs: `docs/reference/agent-adapters.md` section + `docs/guides/
   personalization.md` short mention.

## Allowed paths

`src/workctx/adapters/agents/**`, `src/workctx/guide.py`,
`src/workctx/cli.py` (status rendering only), `schemas/` (registry schema
only), `docs/reference/agent-adapters.md`, `docs/guides/personalization.md`,
`docs/reference/cli-envelope.md` (rows), `tests/agents_setup/**`,
`tests/cli/**`. Bridges and packaged skill content are FORBIDDEN
(WP-800 owns them right now); context template FORBIDDEN (WP-810 owns
99_meta right now).

## Tests

Custom skill registered+valid renders for claude and codex and survives a
packaged-registry refresh with zero merge-pending; invalid custom
frontmatter fails with the named rule; packaged refresh rewrites skills:
and preserves custom_skills: verbatim; misplaced packaged-section entry
surfaces the migration repair_action; guide names the custom home. Full
gate where the sandbox allows, limits declared.
