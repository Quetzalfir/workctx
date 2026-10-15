# Brief: WP-860 — Topics core: registry, duplicate guard, facets, filters (ADR 0015 §1-3)

Codex worker, worktree `.worktrees/WP-860`, branch `agent/WP-860-topics-core`.
No commits; final message = report. `.agents/` read-only (mirrors and
`_HISTORICAL_TEMPLATE_BRIDGE_HASHES` are lead-reconciled). Read
`docs/adr/0015-topics-heat-and-graph-export.md` FIRST — it is the
contract; this brief scopes the first slice. Heat, checkpoints, and graph
export are OUT of scope (later packages).

## Contract

1. Facet. Entities, tasks, evidence notes, and outbox drafts accept
   optional `topics: [slug, ...]` (unique, kebab-case slugs). Domain
   models + JSON Schemas + ADR 0011-style producer-invariant descriptions.
   Absent = common layer. Existing documents validate unchanged.
2. Registry. Canonical `99_meta/topics.yaml` (schema_version 1): topics
   with `slug`, `name`, `scope` (one line), `aliases` (list), `status`
   (active|merged|archived), `merged_into` (required iff merged),
   `created_at`. Model + JSON Schema. Validation: every document topic
   value must resolve to an active slug or an alias of an active topic;
   otherwise new diagnostic `TOPIC-UNKNOWN` (error) with repair action;
   values naming a merged topic -> `TOPIC-MERGED` (warning, repair: retag
   to merged_into). Register both in DIAGNOSTIC_DEFINITIONS + docs row.
   Registry integrity errors (duplicate slug/alias, alias equals another
   slug, dangling merged_into) -> `TOPIC-REGISTRY` (error).
3. Deterministic duplicate guard (pure function, unit-tested heavily):
   given a candidate name/slug and the registry, return matches using
   normalized comparison (casefold, accent strip, punctuation/stopword
   removal incl. generic qualifiers such as test, prueba, poc, demo,
   v2, numbers, dates), token-subset containment, and edit distance
   (normalized threshold, document it). "Banorte Test 2" vs "banorte" ->
   match. "Banorte Migration API" vs "Banorte" -> containment match.
   Clearly different subjects -> no match.
4. Commands (each mutation builds an audited transaction via the
   existing engine; approval semantics like other mutations, --yes):
   - `workctx topic list [--json] [--all]`
   - `workctx topic add <name> --scope <text> [--slug s] [--alias a ...]
     [--distinct-from <slug> --reason <text>]` — runs the guard; any
     match refuses with the matched topics named and the two remedies
     (reuse/alias the existing topic, or re-run with --distinct-from +
     --reason which is recorded in the registry entry as `distinct_from`
     notes).
   - `workctx topic alias <slug> <alias>`
   - `workctx topic merge <from> <into> --yes` — marks `from` merged,
     moves its aliases + slug into `into`'s aliases, and retags every
     document carrying `from` in ONE transaction.
   - `workctx topic review [--json]` — merge candidates by guard
     similarity between existing topics and by co-reference overlap
     (Jaccard over documents tagged with each); read-only.
5. Retrieval filters: `--topic <slug>` on `search`, `task list`,
   `context-pack`, and `brief`. Semantics: topic-scoped results PLUS the
   common layer (untagged) by default; `--topic-only` excludes common.
   Aliases resolve. Projection gains a topics table/column as needed
   (projections are rebuildable; bump projection version if required).
   MCP: optional `topic` argument on `search`, `task_list`,
   `context_pack` and a new read tool `topic_list` (additive per ADR
   0012; update the contracts/discovery tests).
6. Agent surfaces: packaged `process-evidence`, `curate-knowledge`,
   `manage-tasks`, `draft-replies` gain a short topic step — resolve the
   subject with `workctx topic list` / `workctx topic review`, reuse an
   existing topic or alias, create one only for a durable subject via
   `workctx topic add`, never for a test run, variant, date, or document.
   `workctx guide` routing gains "durable subject -> topic". One sentence
   in each bridge and the template AGENTS.md (extend in place).
   docs/guides/topics.md (new, concise) + cli-envelope rows.

## Allowed paths

`src/workctx/domain/**`, `src/workctx/models/**`, `src/workctx/topics/**`
(new package for registry, guard, commands logic), `src/workctx/validation/**`,
`src/workctx/retrieval/**`, `src/workctx/projections/**` (or wherever the
SQLite projection lives), `src/workctx/views/**` (brief filter only),
`src/workctx/cli.py`, `src/workctx/mcp/**`, `src/workctx/guide.py`,
`schemas/**`, agent kit bridges (3) + the four named packaged skills,
canonical context template AGENTS.md via the sync script,
`docs/guides/topics.md`, `docs/reference/{cli-envelope,validation-diagnostics}.md`,
`tests/**` (new and affected files).

## Tests

Guard matrix (dozens of pairs incl. the Banorte family, accents,
Spanish/English qualifiers, true negatives); registry validation
diagnostics; add/alias/merge transactions end to end incl. merge retag
atomicity and ledger events; review candidates; filters with common-layer
semantics and aliases; MCP parity; content assertions for prompt
surfaces. Full gate where the sandbox allows; limits declared.
