# Brief: WP-600 — Resource directory and status report views (C-203, C-207)

Codex worker, worktree `.worktrees/WP-600`, branch `agent/WP-600-phase2-views`.
You cannot commit; leave changes uncommitted. Final message = report. `.agents/`
is read-only for you. Study `src/workctx/views/` (service, models, rendering)
and `tests/tasks_views/` first — both new views follow the existing pattern
exactly: new `ViewName` members, snapshot data, deterministic renderer,
emitted by `rebuild_views` with the same generated header and hash discipline.

## View 1 — `04_views/resource-directory.md` (C-203)

- Source: entities of type system, service, integration from the projection;
  read each entity's canonical file via `CanonicalStore` to pick up the typed
  `references` list and the optional extra `access_urls` frontmatter field
  (list of {url, label?, access?} mappings; tolerate plain URL strings).
- Group by access requirement (`access` value: public / sso / vpn / other,
  ungrouped last), one table per group: entity title, link(s), one-line
  description (first body line), entity URI.
- Secret discipline: run every rendered line through
  `workctx.validation.engine.contains_possible_secret`; a hit EXCLUDES the
  line and adds a "excluded: possible secret" note naming only the entity ID.

## View 2 — `04_views/status-report.md` (C-207)

- Period: last 7 days relative to the injected clock (clock is already a
  ViewService dependency — no wall-clock reads).
- Sections: Completed (tasks that reached done in period, from task records),
  Moved (status transitions, from ledger events in period — use the existing
  audit summary/read APIs), Blocked and waiting (current blocked/waiting with
  ages), New commitments (tasks created in period with due dates), Evidence
  processed (artifacts archived in period). Every item carries its URI.
- Tone: factual lines suitable for pasting into a manager update; no
  invented narrative, no percentages that the data cannot support.

## Do NOT touch

Anything outside `src/workctx/views/**`, `tests/tasks_views/**`,
`docs/reference/views.md`. If a projection/ledger query you need does not
exist, STOP and report the blocker with the exact query shape you need.
`ViewName` gains two members — verify every exhaustive-match site over
ViewName still passes mypy; if a site outside your paths must change, that is
a BLOCKER, not an edit.

## Tests required

Determinism (two rebuilds byte-identical), content assertions for both views
(fixture context with systems carrying access_urls + tasks with transitions),
secret-exclusion case, empty-context case (views render with "none" sections,
no crash), rebuild-after-delete regression stays green. Full gate:
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
`uv run pytest`. Report exact results; sandbox pytest failures must be
declared, never papered over.
