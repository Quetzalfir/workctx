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
