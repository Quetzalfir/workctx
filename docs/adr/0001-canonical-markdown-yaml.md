# ADR 0001: Canonical Markdown and YAML

- Status: accepted
- Date: 2026-07-30

## Context

The product must remain readable, portable, versionable, and recoverable without a database or one AI vendor.

## Decision

Use Markdown with YAML frontmatter for narrative canonical entities and YAML/JSON for machine-oriented manifests and ledgers. SQLite, FTS, views, and graphs are derived projections.

## Consequences

- users retain inspectable files and Git history;
- serialization and schema validation must be deterministic;
- projection rebuild is a release-critical capability;
- large binary evidence remains preserved outside Markdown but receives manifests and locators.
