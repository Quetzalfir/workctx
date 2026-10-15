# Topics

Topics are durable-subject facets for accounts, initiatives, systems, and workstreams. They
are not folders and should not be created for a test run, variant, date, or individual
document. Canonical definitions live in `99_meta/topics.yaml`; a document without `topics`
belongs to the common layer.

Before creating a topic, inspect the registry and deterministic merge candidates:

```text
workctx topic list
workctx topic review
```

Reuse an existing slug or alias whenever it names the same subject. To propose a new topic:

```text
workctx topic add "Payments Modernization" --scope "Program replacing the payments platform"
```

Topic mutations preview by default. Add `--yes` to approve the audited transaction. The
duplicate guard compares accent- and case-normalized subjects after removing punctuation,
stopwords, generic run qualifiers, versions, numbers, and date fragments. It also detects
token-subset containment and uses normalized Levenshtein distance at a threshold of `0.20`
for spellings with at least five compact characters. A match is refused with two choices:
reuse the existing topic (or add an alias), or record a reviewed exception with both
`--distinct-from <slug>` and `--reason <text>`.

Add a known alternate spelling with `workctx topic alias <slug> <alias> --yes`. Consolidate a
duplicate with `workctx topic merge <from> <into> --yes`; the registry lifecycle change and
all document retagging commit atomically as one audited transaction.

Use `--topic <slug-or-alias>` with `search`, `task list`, `context-pack`, or `brief`. Topic
results include untagged common-layer documents by default. Add `--topic-only` to return only
documents explicitly tagged with the selected topic.

## Computed heat

Topic heat is a deterministic, computed signal; it is never authored in canonical files. For
each active topic, an evidence-note or task creation/update timestamp and each committed ledger
write to a currently tagged canonical document contributes:

```text
2 ** (-age_days / heat_half_life_days)
```

The half-life defaults to seven days. Configure it from 1 through 90 days in `context.yaml`:

```yaml
topics:
  heat_half_life_days: 7
```

Heat buckets use the context's interpolated score quartiles, bounded by absolute floors: `cool`
requires `0.5`, `warm` requires `1.5`, and `hot` requires `3.0`. The floors keep a sparse
context from labeling one recent item hot. `workctx topic heat` lists active topics in
hot-to-cold order with score and last activity; `workctx topic list`, MCP `topic_list`, the
generated `04_views/topics.md`, and the `workctx brief` hot-topics section use the same signal.

## Session checkpoints

A checkpoint records only canonical item URIs, context-relative source paths, revisions, and
SHA-256 content hashes. It never copies item bodies or secret values. Save previews by default
and commits through the audited transaction engine only with approval:

```text
workctx checkpoint save "handoff" --topic payments-modernization \
  --ref workctx://example/task/TASK-2026-001 --yes
workctx checkpoint save "pack handoff" --from-pack outputs/context-pack.json --yes
```

Checkpoint files live directly under `99_meta/` as `CHK-*.yaml`. This canonical location keeps
saves atomic in older contexts because `99_meta/` already exists; no unaudited parent-directory
creation is needed. `--from-pack` accepts a bounded regular JSON file containing either the raw
pack or the JSON CLI envelope and captures the pack's canonical item URIs.

Run `workctx checkpoint list` to find saved IDs and `workctx checkpoint check <id>` to compare a
saved checkpoint with current canonical state. Results are `green` for unchanged items,
`yellow` for changed content or revision, and `red` for deleted, moved, or superseded items;
red results include exact known successor URI/path locations. A topic-scoped checkpoint also
reports `NEW` canonical items bearing that topic whose creation time is after the checkpoint.
MCP `checkpoint_check` returns the same typed read result.
