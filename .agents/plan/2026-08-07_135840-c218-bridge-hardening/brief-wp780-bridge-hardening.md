# Brief: WP-780 — Orient-first, ask-once bridge hardening (C-218)

Codex worker, worktree `.worktrees/WP-780`, branch `agent/WP-780-bridge-hardening`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-218 in `.agents/status/phase2-candidates.md`
first, then the current bridges under
`src/workctx/resources/agent_kit/bridges/`, the context template
`AGENTS.md`, and the `bootstrap-session` and `trace-context` skills.

## Problem

Agents operating inside real contexts ignore stored knowledge: they ask
the operator for facts the context already holds (access methods, people
and roles, permission flows), and answers given in chat evaporate — the
next session asks again.

## Contract

Two rules, written as short, imperative, testable bridge sections in all
three bridges (AGENTS.md, CLAUDE.md, GEMINI.md), the context template
AGENTS.md (canonical `templates/context/AGENTS.md`; regenerate the mirror
with `scripts/sync_context_template.py`), and the `bootstrap-session`
skill (plus `trace-context` only if it names lookup surfaces):

1. ORIENT BEFORE ASKING. At task start: read `context.yaml` policies and
   the generated views `04_views/people-directory.md`,
   `resource-directory.md`, `glossary.md`, `current-focus.md` when
   present. Before asking the operator for ANY fact, name, credential
   location, or process: run `workctx search "<topic>"`, check
   `90_integrations/`, `workctx secret list`, `workctx connector list`,
   and relevant entities. Asking the operator something the context
   already answers is a protocol violation. This extends the existing
   access-discovery rule (keep it; broaden its scope wording to all
   context knowledge, not only access).
2. ASK ONCE, RECORD FOREVER. When the operator supplies a fact the
   context lacked, persist it the SAME session through the normal
   approval-gated proposal flow: a person fact -> person entity; an
   access/process fact -> integration entity under `90_integrations/` or
   system entity; a standing preference -> suggest adding it to the
   context `instructions.md` (operator applies it). Never store secret
   VALUES — reference names only (existing rule stays). End-of-session
   check in `bootstrap-session`/close guidance: "did the operator repeat
   or newly supply any fact? If yes, it must be recorded before closing."

Keep the bridges tight: these are additions of one short section each,
not rewrites. Match the existing voice and formatting of each bridge.
English only.

## Allowed paths

`src/workctx/resources/agent_kit/bridges/AGENTS.md`, `CLAUDE.md`,
`GEMINI.md`; `src/workctx/resources/context_template/AGENTS.md` via the
canonical template + `scripts/sync_context_template.py` (run it; do not
hand-edit generated mirrors); `src/workctx/resources/agent_kit/skills/
bootstrap-session/SKILL.md` and `trace-context/SKILL.md`;
`.agents/skills/` mirrors ONLY via the existing sync mechanism if one
covers them (otherwise leave and report); `tests/agents_setup/**`
(content assertions), `tests/skills/**` if skill lint tests live there.
Nothing else — no engine code, no cli.py, no schemas.

## Tests required

Content tests pinning both rules present in all three bridges and the
template (assert on distinctive phrases, e.g. the protocol-violation
sentence and the record-before-closing check); skill lint stays green;
every existing bridge/template/personalization test passes (personalized
render must still merge cleanly with the new sections). Run every gate
you can; declare sandbox limits explicitly.
