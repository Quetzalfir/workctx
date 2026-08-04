# Brief: WP-640 — Code-repositories guide + curation tiering (C-209)

Claude agent, own worktree. Final message = report. `.agents/` read-only —
read C-209 in `.agents/status/phase2-candidates.md` first.

## Scope

1. NEW `docs/guides/code-repositories.md`:
   - the two working modes: referenceable fichas (register a repo once as a
     system/module entity; deep-dive on demand) and active deep review whose
     conclusions persist as observations;
   - `repo://<repo-id>@<commit>/<path>#L<start>-L<end>` locator usage with a
     worked example (cost-investigation narrative, fictional);
   - one-time `gh auth login` machine setup; explicit note that tokens never
     enter chat or the workspace and that first-class secret references are
     the workctx-native path (cross-link docs/reference/secrets.md IF it
     exists on your branch, otherwise omit the link);
   - the investigate-system skill flow from a user's point of view;
   - the three-tier curation rule (below) applied to repos and URLs.
2. Three-tier curation rule added to BOTH
   `src/workctx/resources/agent_kit/skills/curate-knowledge/SKILL.md` and
   `process-evidence/SKILL.md`, in each skill's existing voice and structure:
   tier-1 entity (core repos/systems: own ficha, typed relations, resource
   directory, accumulates observations), tier-2 reference (supporting
   sources: `references` entry or evidence-note source ref; searchable, no
   entity), tier-3 nothing (one-off links: at most a mention in a note
   body). Default when unsure: tier 2; promote to tier 1 on second real use.
3. One cross-link line in `docs/guides/multiple-contexts.md` pointing to the
   new guide (repos belong to the project context that owns them; a repo is
   not a context).

## Do NOT touch

Anything else. English only; fictional examples only; no claims about
unimplemented behavior — every command you mention must exist on your branch
(verify with --help); skill edits must keep the skill lint green (no
unimplemented product references without "(planned)").

## Acceptance

Relative links resolve; `uv run ruff check .` and
`uv run ruff format --check .` pass; `uv run pytest tests/test_skills.py
tests/agents_setup -q` passes (skill lint); full pytest if your environment
allows — report exact results either way.
