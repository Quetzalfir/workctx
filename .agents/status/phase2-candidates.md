# Phase 2 candidates (operator-requested, 2026-08-03)

Two feature themes requested by the operator after the Phase 1 close. Both are
recorded in ROADMAP.md under Phase 2; this file holds the design intent so the
future wave lead does not have to reconstruct it from conversation.

## C-201 — Portable personalization layers

Problem: today per-user and per-project instructions ("answer in this tone",
"in this situation do not make changes", "our role is X") live in each agent's
own mechanism (e.g. the Claude global/project bridge files), so they are
written per agent and drift apart.

Intent: two canonical instruction layers owned by workctx —

1. user-level, one file per machine user (location: the same per-user
   directory that already holds the context registry);
2. context-level, one canonical file inside the workspace.

The agent installer merges both layers into every generated bridge/adapter at
install and upgrade time, for all supported clients. Precedence: context over
user. Layers are plain Markdown, validated only for size and secret-scan.
Upgrades must never discard a layer (three-factor install records already
distinguish generated from user-owned content).

## C-202 — Assisted improvement loop

Problem: the assistant should notice quality drift — missing references, stale
structure, weak output contracts, better processing steps — and improve the
system, but improvements must not bypass review or mutate engine behavior.

Intent: three explicitly separated improvement targets —

1. workspace data: already served by validation diagnostics, stale-knowledge
   views, and ref tracing; suggestions become ordinary approved transactions;
2. local skills/templates: a suggestion workflow (finding -> reviewable
   suggestion -> adopted local override) where adopted overrides are versioned
   in the context and survive kit upgrades; needs an override mechanism in the
   skill loader plus provenance so an upgrade can show a three-way diff;
3. the workctx engine itself: out of runtime scope by design — the agent may
   draft an issue/proposal for the open-source repository, never hot-modify
   deterministic behavior.

Constraint carried from Phase 1: suggestions are data, not instructions; the
loop must not create a channel where evidence content can smuggle behavior
changes into skills.
