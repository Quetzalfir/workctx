# Concepts

## Context

A context is an isolated company, project, product, or personal-work boundary. It has a stable ID, a root directory, canonical entities, generated state, agent configuration, and connector references.

## Artifact

An artifact is preserved source material such as a transcript, chat, screenshot, document, export, or repository snapshot. Its immutable content hash prevents accidental duplicate processing.

## Evidence note

An evidence note is a readable synthesis of one or more artifacts. It contains atomic observations that point to exact source locators.

## Observation

An observation is the smallest traceable statement extracted from evidence. It is classified as a fact, inference, assumption, decision, commitment, task signal, risk, blocker, dependency, or question.

## Claim

A claim represents an assertion that may change over time, such as a task status, owner, deadline, or active architecture decision. Superseded claims remain available for history.

## Entity

A durable person, team, project, system, service, module, flow, integration, decision, risk, question, or task with a stable ID and canonical URI.

## Typed relation

A semantic edge such as `supports`, `contradicts`, `depends_on`, `blocks`, `owned_by`, or `calls`. Backlinks are generated from canonical outbound relations.

## Transaction

A validated, atomic set of canonical mutations. One failed operation prevents partial canonical updates.

## Projection

A rebuildable representation such as SQLite/FTS indexes, backlinks, current-focus views, or optional graphs.

## Context pack

A bounded retrieval bundle around a task, person, system, or question. It combines current state, related work, people, decisions, risks, evidence, and history within a size budget.
