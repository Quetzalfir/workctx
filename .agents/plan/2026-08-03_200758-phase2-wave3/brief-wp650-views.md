# Brief: WP-650 — People directory, glossary, agenda, and suggestions views

Codex worker, worktree `.worktrees/WP-650`, branch `agent/WP-650-more-views`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-204/C-205/C-206 and C-202 in
`.agents/status/phase2-candidates.md` first. Follow the exact pattern of the
seven existing views (models, snapshot, renderer, header, hash); study
`src/workctx/views/` and `tests/tasks_views/test_generated_views_wp600.py`.

## View 1 — `people-directory.md` (C-204)

Person and team entities (query_entities): role/team/channels from frontmatter
extras and typed references (tolerate absence), timezone when present, and per
person the tasks they currently own or block and what waits on them (join with
task records). Alphabetical by title within person/team sections.

## View 2 — `glossary.md` (C-205)

Every entity alias (aliases are already indexed) mapped to entity title, type,
URI, and the first non-empty body line as definition. Alphabetical,
case-insensitive, aliases-only view — entities without aliases are absent.
Duplicate alias across entities: list every owner under the alias.

## View 3 — `agenda.md` (C-206)

Date-ordered horizon: tasks with `due_at` (ascending, overdue flagged
factually by comparison with the injected clock), then waiting-on entries with
ages (from the existing waiting-on computation), then blocked tasks with
blocker ages. Every line carries the task URI.

## View 4 — `suggestions.md` (C-202 detection half)

Signals derivable ONLY from canonical/audit state via existing read APIs — no
telemetry, no usage counters:

- stale claims beyond the stale_after horizon (existing computation) phrased
  as "review or supersede";
- tasks whose every source observation no longer resolves (use retrieval
  trace APIs) — "evidence link broken";
- active/waiting tasks with no activity in the audit ledger for 30+ days —
  "confirm still real, or close";
- entities never referenced by any task, claim, observation, or relation —
  "orphaned knowledge: connect or archive";
- waiting-on entries older than 14 days — "chase or drop".

Each suggestion: factual one-liner + URI + the signal that produced it. NO
imperative auto-actions, no counts of "uses". Empty sections render "_No
suggestions._" This view is advisory text only — it must never imply the
system will act on its own.

## Do NOT touch

Anything outside `src/workctx/views/**`, `tests/tasks_views/**`,
`docs/reference/views.md`. Missing query = blocker with the exact shape.
ViewName grows by four — the migration test counts the enum and must keep
passing untouched; any other exhaustive-match break outside your paths is a
BLOCKER, not an edit.

## Tests required

Determinism, content assertions per view (fixture with people/teams/aliases/
due dates/stale claims/orphans), duplicate-alias case, empty-context case,
rebuild-after-delete regression, secret discipline (run rendered lines through
contains_possible_secret where content originates in entity bodies, as the
resource directory does). Full gate; declare sandbox limitations explicitly.
