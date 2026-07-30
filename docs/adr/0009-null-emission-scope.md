# ADR 0009: Null-emission scope for canonical serialization

- Status: accepted
- Date: 2026-07-30

## Context

ADR 0005 requires emitting all declared fields including nulls so that omission-vs-null is
never a per-writer choice. Wave 1 integration surfaced a conflict: the reference contracts
(ADR 0008 schemas owned by WP-100) declare nested optional fields that are non-nullable
(`confidence`, `note`, relation validity fields), so emitting `field: null` inside a nested
reference would violate the public schema. WP-110 documented the collision and applied a
scoped exception; the lead must reconcile the rule (recorded unresolved item in the WP-110
report).

## Decision

Refine — do not reverse — ADR 0005's emission rule:

- Top-level declared fields of a canonical document are always emitted in declaration
  order; schema-nullable fields emit `field: null` when absent (e.g. `valid_to: null`).
- Nested optional fields whose JSON Schema declares them non-nullable are **omitted** when
  absent. Emitting null there would violate the hand-maintained public contract, which
  wins (ADR 0008).
- A field's nullability in the JSON Schema is therefore the single source of truth for
  null-vs-omit; serializers must not decide per call site.
- Contract fixtures must include at least one absent-optional case per document type so
  the chosen behavior stays pinned.

## Consequences

- The integrated WP-100 and WP-110 deliveries already comply; no code changes required.
- Determinism is preserved: the rule is schema-driven, not writer-driven.
- ADR 0005 remains in force for everything else; this ADR supersedes only its universal
  null-emission sentence.
