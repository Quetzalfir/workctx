# Brief: WP-800 — Skills name their exact commands; skill-path guidance (realignment wave)

Codex worker, worktree `.worktrees/WP-800`, branch `agent/WP-800-skill-commands`.
No commits; final message = report. `.agents/` read-only (mirrors are
lead-reconciled). Origin: 2026-08-05 audit findings — agents guess at CLI
verbs because skills describe workflows abstractly; and a live 2026-08-18
incident: an agent declared trace-context "not accessible" because it
looked relative to its working directory instead of the context root.

## Contract

1. Every packaged skill under `src/workctx/resources/agent_kit/skills/`
   names the EXACT `workctx` commands its workflow uses, at the step where
   they are used (inline verbs plus a short "Commands used" list at the
   end of each SKILL.md). Verify every named command exists by running
   `uv run workctx --help` and subcommand helps in the worktree; naming a
   nonexistent command is a defect. Keep each skill's voice; no rewrites.
2. Mutation-class skills additionally cite
   `99_meta/schemas/transaction-proposal.schema.json` (phrase it "when
   present") as the authoritative proposal shape reference.
3. One skill-path sentence in the three bridges and the context template
   AGENTS.md, extending the existing C-218/C-219 sections in place:
   skills live at `<context root>/.agents/skills/` (Claude renders under
   `.claude/skills/`); resolve them from the context root, never the
   current working directory; before declaring a skill unavailable, list
   that directory.

## Allowed paths

`src/workctx/resources/agent_kit/skills/**` (SKILL.md files),
`src/workctx/resources/agent_kit/bridges/*.md`,
`src/workctx/resources/context_template/AGENTS.md` + sync script run for
the mirror, `tests/agents_setup/**` and `tests/test_skills.py` (content
assertions). Do NOT touch `99_meta` template content, engine code,
cli.py, or schemas/ (a sibling package owns those).

## Tests

Content tests: the skill-path sentence pinned in all three bridges +
template; every "Commands used" entry matches the real command surface
(derive the allowed verb list from the Typer app or pin it explicitly);
skill lint suite green; full gate where the sandbox allows, limits
declared.
