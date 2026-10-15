# ADR 0015: Topics, heat, checkpoints, and graph export

Status: Accepted (2026-10-14)

## Context

Contexts accumulate several parallel subjects inside one project (client
accounts, initiatives, investigations). Today they are linked only through
free references, so agents mix unrelated material, a paused session cannot
tell what changed while it was idle, and nothing says which subjects are
currently active. Operators want subjects recognized without having to name
them, without duplicate near-identical subjects ("Banorte", "Banorte Test",
"Banorte Test 2"), and a visual map of subjects and their links.

## Decision

1. **Topics are a facet inside a context, not nested contexts.** Entities,
   tasks, evidence notes, and drafts carry optional `topics: [slug, ...]`.
   Material without topics is the shared common layer. Nested contexts were
   rejected because the isolation invariant would make the common layer
   unreachable from a topic.

2. **A canonical topic registry** at `99_meta/topics.yaml` holds each topic's
   slug, display name, one-line scope, aliases, status (`active`, `merged`,
   `archived`), and `merged_into` for merged topics. Every `topics` value must
   resolve to an active registry slug or an alias; validation reports unknown
   slugs with a repair action.

3. **Agents identify topics by default; operators may name them.** A topic is a
   durable subject — an account, initiative, system, or workstream — never an
   instance, test run, date, or document. Before proposing a new topic an
   agent must resolve the subject against existing topics and aliases. A
   deterministic duplicate guard runs on every proposal that creates a topic:
   normalized slug and alias comparison, token-subset containment, and edit
   distance; a candidate that matches an existing topic is rejected with the
   match named, unless the proposal explicitly records it as a distinct topic
   with a reason. Test runs and variants become events, tasks, or tags under
   the existing topic. `workctx topic merge` folds a duplicate into its
   canonical topic transactionally (registry aliases plus retagging), and
   `workctx topic review` lists merge candidates by name similarity and
   co-reference overlap.

4. **Heat is computed, never authored.** A topic's heat is a deterministic,
   recency-weighted activity score over its tagged ledger events, evidence,
   and task changes, bucketed as hot, warm, cool, or cold. It appears in
   `04_views/topics.md`, in topic listings, and as a priority signal in the
   brief and evidence-processing workflows.

5. **Checkpoints give a staleness signal.** A session may save a checkpoint
   recording the canonical items it relied on and their revisions. Checking a
   checkpoint later reports each item as unchanged (green), changed (yellow),
   or superseded, moved, or deleted (red), with the location of the newer
   material. This is how a paused thread picks up fresh context.

6. **Everything processed becomes evidence.** Packaged workflows require that
   findings from any reviewed thread, channel, or source be reconciled into
   the context in the same session as evidence and events under the relevant
   topics, through the normal approval-gated proposal flow.

7. **Graph export, Obsidian optional.** `workctx graph export` generates
   read-only notes under `04_views/graph/` in two separate maps: a topic map
   (one note per topic, links to topics sharing references, heat as tags) and
   a reference map (each topic linked to the items that support it). It also
   writes Obsidian color-group settings mapping heat to color (hot red through
   cold purple); Obsidian sizes nodes by link count on its own.
   `workctx obsidian install` installs Obsidian through the platform package
   manager after an explicit confirmation. workctx remains fully usable
   without Obsidian.

## Consequences

The change is additive: existing contexts validate unchanged, and all current
material is the common layer until topics are assigned. Topic quality depends
on the duplicate guard and on review and merge, so both ship with the first
topics release rather than later.
