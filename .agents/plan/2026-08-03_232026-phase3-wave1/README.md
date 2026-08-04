# Phase 3 — Wave 1 (operator-approved overlap with Phase 2 wave 4, D-048)

First Phase 3 package: the C-214 generic declarative connector runtime.
Runs in parallel with WP-680/WP-690 (Phase 2 wave 4); disjoint paths.
Connector CLI wiring deferred to integration (WP-680 owns cli.py).

## Package

| Package | Scope | Worker |
| --- | --- | --- |
| WP-710 | Manifest spec + snapshot engine + inbox registration, service API only (no CLI) | Codex (max) |

## Path ownership

- WP-710: `src/workctx/connectors/**` (new), `tests/connectors/**` (new;
  layout-guard-safe name), `schemas/connector-manifest.schema.json` +
  fixtures, `docs/reference/connectors.md` (new).
- Explicitly NOT WP-710: `src/workctx/cli.py` (WP-680 owns it), any
  background scheduling daemon (v1 is manual/agent-invoked sync; the
  manifest `schedule` field is recorded metadata for future automation).

## Wave-close criteria

1. Full gate + matrix green integrated alongside Phase 2 wave 4.
2. A fictional manifest against a mocked transport produces a snapshot
   artifact in 00_inbox with full provenance and enters the normal
   registration pipeline (quarantine rules untouched and proven to apply).
3. Secret values resolved via secret_ref never appear in snapshots,
   manifests, logs, envelopes, or errors.
4. Lead wires `workctx connector sync|list` after WP-680 integrates.
