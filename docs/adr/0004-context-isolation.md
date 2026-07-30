# ADR 0004: One context per security boundary

- Status: accepted
- Date: 2026-07-30

## Context

A user may work for several companies and on personal projects from one computer.

## Decision

Treat each context root as an isolated security boundary with its own canonical files, indexes, plugin configuration, secret references, and audit records. Deny federated search by default.

## Consequences

- every operation requires an explicit resolved context handle;
- canonical URIs include context ID;
- test fixtures must verify no cross-context leakage;
- a company may contain multiple internal projects if they share the same authorized boundary.
