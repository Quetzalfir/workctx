# Brief: WP-870 — Topic heat, checkpoint staleness, evidence-everything (ADR 0015 §4-6)

Codex worker, worktree `.worktrees/WP-870`, branch `agent/WP-870-heat-checkpoints`.
No commits; final message = report. `.agents/` read-only (mirrors and
`_HISTORICAL_TEMPLATE_BRIDGE_HASHES` are lead-reconciled). Read ADR 0015
and the WP-860 topics package (`src/workctx/topics/`, projection topic
indexing) first. Graph export / Obsidian are OUT of scope (WP-880).

## Contract

1. Heat (§4). Pure deterministic function: per active topic, a
   recency-weighted score over activity attributable to the topic —
   committed ledger events whose written documents carry the topic,
   evidence notes and tasks with the topic by their timestamps. Use
   exponential decay with a documented half-life (default 7 days,
   configurable in context.yaml `topics.heat_half_life_days`, 1-90),
   evaluated against an injectable clock. Buckets hot/warm/cool/cold by
   documented thresholds relative to the context's own distribution
   (e.g. quantiles with absolute floors so a near-empty context is not
   all "hot"). Outputs: `workctx topic list` shows heat and score;
   `workctx topic heat [--json]` lists topics sorted by heat with the
   last-activity date; new generated view `04_views/topics.md` (ViewName
   enum, view rebuild, view-count anchors updated); `workctx brief`
   gains a "Hot topics" section. MCP `topic_list` includes heat.
2. Checkpoints (§5). Canonical checkpoints under
   `99_meta/checkpoints/<id>.yaml` (or another canonical home justified
   in the report), written via audited transaction: id, created_at,
   label, optional topic, items = [{uri, revision/content hash at save}].
   `workctx checkpoint save <label> [--topic t] --ref <uri> ...
   [--yes]` (and `--from-pack`: capture the items of the last
   `context-pack` output file if given); `workctx checkpoint check <id>
   [--json]` reports per item green (unchanged), yellow (changed — with
   the newer revision and what changed at document level), red
   (deleted, moved, superseded — with where the successor is), plus NEW
   items under the checkpoint's topic created since it was saved;
   `workctx checkpoint list`. MCP read tool `checkpoint_check`
   (additive). Checkpoint content never includes secret values.
3. Evidence-everything (§6). Packaged `process-evidence`,
   `bootstrap-session`, `close-session` skills and the three bridges +
   template AGENTS.md: findings from any reviewed thread, channel,
   meeting, or source are reconciled into the context in the same
   session as evidence/events under the relevant topics through the
   normal proposal flow; at session start check the session's
   checkpoint (if any) and surface yellow/red items first; at close,
   save a checkpoint of what the session relied on; hot topics get
   priority when triaging new findings. Extend existing sections in
   place (no competing rules); keep it short.
4. Docs: docs/guides/topics.md sections for heat and checkpoints;
   cli-envelope rows; validation-diagnostics rows for any new codes.

## Allowed paths

`src/workctx/topics/**`, `src/workctx/checkpoints/**` (new),
`src/workctx/domain/**`, `src/workctx/models/**`, `src/workctx/views/**`,
`src/workctx/retrieval/**`, `src/workctx/projections/**` (or the
projection module), `src/workctx/validation/**`, `src/workctx/cli.py`,
`src/workctx/mcp/**`, `src/workctx/guide.py`, `schemas/**`, agent kit
bridges (3) + packaged process-evidence/bootstrap-session/close-session
skills, canonical context template AGENTS.md via the sync script,
`docs/guides/topics.md`, `docs/reference/{cli-envelope,validation-diagnostics}.md`,
`tests/**`.

## Tests

Heat: decay math, bucket thresholds incl. near-empty contexts, injectable
clock determinism, view/brief/list output. Checkpoints: save via
transaction + ledger, check matrix green/yellow/red/new with exact
successor locations, MCP parity. Prompt-surface content assertions.
Full gate where the sandbox allows; limits declared.
